package handler

import (
	"context"
	"github.com/gogo/protobuf/jsonpb"
	"github.com/michelangelo-ai/michelangelo/go/auth"
	apipb "github.com/michelangelo-ai/michelangelo/proto-go/api"
	v2 "github.com/michelangelo-ai/michelangelo/proto-go/api/v2"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"strings"
	"testing"
)

func releasePrincipal(service bool) context.Context {
	subject := "human"
	if service {
		subject = publicationService
	}
	return auth.WithOCIPrincipal(context.Background(), auth.OCIPrincipal{ID: subject, Subject: subject, Enabled: true, Service: service, Role: "operator"})
}
func TestReleaseCandidateStartsPending(t *testing.T) {
	m := &v2.Model{ObjectMeta: metav1.ObjectMeta{Name: "candidate", Namespace: "project", Labels: map[string]string{publicationPrefix + "eligible": "true"}, Annotations: map[string]string{publicationPrefix + "publication-state": "eligible", publicationPrefix + "publication-proof-sha256": "forged"}}}
	if err := validatePublicationMutation(releasePrincipal(false), m, nil, nil); err != nil {
		t.Fatal(err)
	}
	if m.Labels[publicationPrefix+"eligible"] != "false" || m.Annotations[publicationPrefix+"publication-state"] != "pending" || m.Annotations[publicationPrefix+"publication-proof-sha256"] != "" {
		t.Fatal("client spoofed publication")
	}
}
func TestReleaseCannotSelfFinalize(t *testing.T) {
	old := &v2.Model{ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{publicationPrefix + "eligible": "false"}, Annotations: map[string]string{publicationPrefix + "publication-state": "pending"}}}
	next := old.DeepCopy()
	next.Labels[publicationPrefix+"eligible"] = "true"
	next.Annotations[publicationPrefix+"publication-state"] = "eligible"
	if err := validatePublicationMutation(releasePrincipal(false), next, old, nil); err != nil {
		t.Fatal(err)
	}
	if next.Labels[publicationPrefix+"eligible"] != "false" || next.Annotations[publicationPrefix+"publication-state"] != "pending" {
		t.Fatal("client self-finalized")
	}
}

func TestReleaseCandidateCannotReuseNativeIdentity(t *testing.T) {
	m := &v2.Model{ObjectMeta: metav1.ObjectMeta{UID: "previous-candidate"}}
	if validatePublicationMutation(releasePrincipal(false), m, nil, nil) == nil {
		t.Fatal("client supplied native candidate identity")
	}
	old := m.DeepCopy()
	m.UID = "changed"
	if validatePublicationMutation(releasePrincipal(false), m, old, nil) == nil {
		t.Fatal("client changed native candidate identity")
	}
}
func TestReleaseDeploymentChecksActualSource(t *testing.T) {
	state := v2.PIPELINE_RUN_STATE_FAILED
	candidate := &v2.Model{ObjectMeta: metav1.ObjectMeta{Name: "candidate", Namespace: "project", Labels: map[string]string{publicationPrefix + "eligible": "true"}, Annotations: map[string]string{publicationPrefix + "publication-state": "eligible"}}, Spec: v2.ModelSpec{SourcePipelineRun: &apipb.ResourceIdentifier{Name: "run", Namespace: "project"}}}
	get := func(_ context.Context, _, _ string, _ *metav1.GetOptions, obj client.Object) error {
		switch dst := obj.(type) {
		case *v2.Model:
			*dst = *candidate
		case *v2.PipelineRun:
			dst.Status.State = state
		}
		return nil
	}
	deployment := &v2.Deployment{ObjectMeta: metav1.ObjectMeta{Namespace: "project"}, Spec: v2.DeploymentSpec{DesiredRevision: &apipb.ResourceIdentifier{Name: "candidate", Namespace: "project"}}}
	if validatePublicationMutation(releasePrincipal(false), deployment, nil, get) == nil {
		t.Fatal("failed source deployed")
	}
	state = v2.PIPELINE_RUN_STATE_SUCCEEDED
	if err := validatePublicationMutation(releasePrincipal(false), deployment, nil, get); err != nil {
		t.Fatal(err)
	}
	candidate.Annotations[publicationPrefix+"publication-state"] = "pending"
	if validatePublicationMutation(releasePrincipal(false), deployment, nil, get) == nil {
		t.Fatal("pending candidate deployed")
	}
}
func TestReleaseMaintenanceWorkClassification(t *testing.T) {
	run := &v2.PipelineRun{}
	if !startsWork(run, nil) {
		t.Fatal("new run not gated")
	}
	old := run.DeepCopy()
	run.Spec.Kill = true
	if startsWork(run, old) {
		t.Fatal("cancellation incorrectly blocked")
	}
	trigger := &v2.TriggerRun{}
	oldTrigger := trigger.DeepCopy()
	trigger.Spec.Action = v2.TRIGGER_RUN_ACTION_PAUSE
	if startsWork(trigger, oldTrigger) {
		t.Fatal("pause incorrectly blocked")
	}
	trigger.Spec.Action = v2.TRIGGER_RUN_ACTION_RESUME
	if !startsWork(trigger, oldTrigger) {
		t.Fatal("resume not gated")
	}
}

func TestReleaseMaintenanceFailClosed(t *testing.T) {
	k, err := setupK8sWithoutReleaseGate()
	if err != nil {
		t.Fatal(err)
	}
	h := NewFakeAPIHandler(k).(*apiHandler)
	ctx := releasePrincipal(false)
	run := &v2.PipelineRun{}
	if h.validateReleaseMutation(ctx, run, nil) == nil {
		t.Fatal("missing admission state permitted work")
	}
	config := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "ma-release-control", Namespace: "ma-system"}, Data: map[string]string{"maintenance": "true"}}
	if err := k.Create(ctx, config); err != nil {
		t.Fatal(err)
	}
	if h.validateReleaseMutation(ctx, run, nil) == nil {
		t.Fatal("maintenance permitted work")
	}
	old := run.DeepCopy()
	run.Spec.Kill = true
	if err := h.validateReleaseMutation(ctx, run, old); err != nil {
		t.Fatal("maintenance blocked cancellation", err)
	}
	config.Data["maintenance"] = "false"
	if err := k.Update(ctx, config); err != nil {
		t.Fatal(err)
	}
	if err := h.validateReleaseMutation(ctx, &v2.PipelineRun{}, nil); err != nil {
		t.Fatal(err)
	}
}

func TestReleaseFinalizerCannotChangeVerifiedArtifacts(t *testing.T) {
	old := &v2.Model{ObjectMeta: metav1.ObjectMeta{Annotations: map[string]string{publicationPrefix + "publication-state": "pending", publicationPrefix + "package-sha256": strings.Repeat("a", 64)}}}
	next := old.DeepCopy()
	next.Annotations[publicationPrefix+"package-sha256"] = strings.Repeat("b", 64)
	if validatePublicationMutation(releasePrincipal(true), next, old, nil) == nil {
		t.Fatal("finalizer changed provenance")
	}
	next = old.DeepCopy()
	next.Spec.DeployableArtifactUri = []string{"s3://other/model"}
	if validatePublicationMutation(releasePrincipal(true), next, old, nil) == nil {
		t.Fatal("finalizer changed model artifacts")
	}
}

func TestReleaseResumeCannotBypassModelAdmission(t *testing.T) {
	k, err := setupK8sWithoutReleaseGate()
	if err != nil {
		t.Fatal(err)
	}
	h := NewFakeAPIHandler(k).(*apiHandler)
	ctx := releasePrincipal(false)
	config := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "ma-release-control", Namespace: "ma-system"}, Data: map[string]string{"maintenance": "false"}}
	candidate := &v2.Model{ObjectMeta: metav1.ObjectMeta{Name: "pending", Namespace: "project", Annotations: map[string]string{publicationPrefix + "publication-state": "pending"}}}
	deployment := &v2.Deployment{ObjectMeta: metav1.ObjectMeta{Name: "deployment", Namespace: "project"}, Spec: v2.DeploymentSpec{Target: &v2.DeploymentSpec_InferenceServer{InferenceServer: &apipb.ResourceIdentifier{Name: "server", Namespace: "project"}}, DesiredRevision: &apipb.ResourceIdentifier{Name: "pending", Namespace: "project"}}}
	for _, obj := range []client.Object{config, candidate, deployment} {
		if err := k.Create(ctx, obj); err != nil {
			t.Fatal(err)
		}
	}
	target := &v2.InferenceServer{ObjectMeta: metav1.ObjectMeta{Name: "server", Namespace: "project"}}
	old := target.DeepCopy()
	old.Spec.Suspended = true
	if h.validateReleaseMutation(ctx, target, old) == nil {
		t.Fatal("resume bypassed pending model admission")
	}
	target.Spec.Suspended = true
	if err := h.validateReleaseMutation(ctx, target, old); err != nil {
		t.Fatal("suspension blocked", err)
	}
}

func TestReleaseCombinedCancelRetryIsWork(t *testing.T) {
	old := &v2.PipelineRun{}
	next := old.DeepCopy()
	next.Spec.Kill = true
	next.Spec.RetryInfo = &v2.RetryInfo{ActivityId: "activity", WorkflowRunId: "workflow"}
	if !startsWork(next, old) {
		t.Fatal("cancel plus retry bypassed maintenance")
	}
}
func TestReleaseDirectComputeBlockedAndWorkerMayDrain(t *testing.T) {
	k, err := setupK8sWithoutReleaseGate()
	if err != nil {
		t.Fatal(err)
	}
	h := NewFakeAPIHandler(k).(*apiHandler)
	human := releasePrincipal(false)
	config := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "ma-release-control", Namespace: "ma-system"}, Data: map[string]string{"maintenance": "true"}}
	if err := k.Create(human, config); err != nil {
		t.Fatal(err)
	}
	worker := auth.WithOCIPrincipal(context.Background(), auth.OCIPrincipal{ID: "worker", Subject: "system:serviceaccount:ma-system:michelangelo", Enabled: true, Service: true, Role: "operator"})
	for _, obj := range []client.Object{&v2.RayJob{}, &v2.SparkJob{}} {
		if h.validateReleaseMutation(human, obj, nil) == nil {
			t.Fatal("human launched compute during maintenance")
		}
		if err := h.validateReleaseMutation(worker, obj, nil); err != nil {
			t.Fatal("worker could not drain", err)
		}
	}
}

func TestReleaseFinalizationPreservesSpecAcrossJSONRoundTrip(t *testing.T) {
	old := &v2.Model{ObjectMeta: metav1.ObjectMeta{Name: "candidate", Namespace: "project", UID: "native-id", Labels: map[string]string{publicationPrefix + "eligible": "false"}, Annotations: map[string]string{publicationPrefix + "publication-state": "pending"}}, Spec: v2.ModelSpec{Description: "synthetic model", ModelArtifactUri: []string{"s3://example/model"}}}
	wire, err := (&jsonpb.Marshaler{EmitDefaults: true}).MarshalToString(&old.Spec)
	if err != nil {
		t.Fatal(err)
	}
	next := old.DeepCopy()
	if err := jsonpb.Unmarshal(strings.NewReader(wire), &next.Spec); err != nil {
		t.Fatal(err)
	}
	next.Annotations[publicationPrefix+"publication-state"] = "quarantined"
	if err := validatePublicationMutation(releasePrincipal(true), next, old, nil); err != nil {
		t.Fatalf("equivalent API round trip rejected: %v", err)
	}
	next.Spec.Description = "changed inputs"
	if validatePublicationMutation(releasePrincipal(true), next, old, nil) == nil {
		t.Fatal("actual specification change accepted")
	}
}
