package handler

import (
	"context"
	"github.com/michelangelo-ai/michelangelo/go/auth"
	apipb "github.com/michelangelo-ai/michelangelo/proto-go/api"
	v2 "github.com/michelangelo-ai/michelangelo/proto-go/api/v2"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"testing"
)

func runtimeContext() context.Context {
	return auth.WithPrincipal(context.Background(), auth.Principal{Enabled: true, Service: true, Role: "operator", Subject: "system:serviceaccount:default:michelangelo-lifecycle-runner"})
}
func TestRunnerDirectHandlerDenialsBeforeStorage(t *testing.T) {
	h := &apiHandler{}
	ctx := runtimeContext()
	cases := []struct {
		name string
		call func() error
	}{
		{"Get platform", func() error { return h.Get(ctx, "ma-system", "secret", &metav1.GetOptions{}, &v2.Cluster{}) }},
		{"Get arbitrary model", func() error {
			return h.Get(ctx, "oci-ranking-showcase", "unrelated", &metav1.GetOptions{}, &v2.Model{})
		}},
		{"List", func() error { return h.List(ctx, "oci-ranking-showcase", &metav1.ListOptions{}, nil, &v2.ModelList{}) }},
		{"Delete", func() error { return h.Delete(ctx, &v2.Model{}, &metav1.DeleteOptions{}) }},
		{"DeleteCollection", func() error {
			return h.DeleteCollection(ctx, &v2.Model{}, "oci-ranking-showcase", &metav1.DeleteOptions{}, &metav1.ListOptions{})
		}},
		{"UpdateStatus", func() error { return h.UpdateStatus(ctx, &v2.Model{}, &metav1.UpdateOptions{}) }},
		{"Create run", func() error { return h.Create(ctx, &v2.PipelineRun{}, &metav1.CreateOptions{}) }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if status.Code(test.call()) != codes.PermissionDenied {
				t.Fatal("unauthorized storage operation reached")
			}
		})
	}
}
func TestRunnerStoredLineage(t *testing.T) {
	ctx := runtimeContext()
	namespace := "oci-ranking-showcase"
	model := &v2.Model{ObjectMeta: metav1.ObjectMeta{Name: "ranking-smoke-0123456789abcdef", Namespace: namespace, Annotations: map[string]string{"michelangelo.ai/demo": "ranking-smoke"}}, Spec: v2.ModelSpec{ModelFamily: &apipb.ResourceIdentifier{Name: "ranking-smoke", Namespace: namespace}}}
	if err := runnerResult(ctx, model); err != nil {
		t.Fatal(err)
	}
	model.Spec.ModelFamily.Name = "ranking-advanced"
	if runnerResult(ctx, model) == nil {
		t.Fatal("cross-family read")
	}
	run := &v2.PipelineRun{ObjectMeta: metav1.ObjectMeta{Name: "run", Namespace: namespace}, Spec: v2.PipelineRunSpec{Pipeline: &apipb.ResourceIdentifier{Name: "ranking-smoke", Namespace: namespace}}}
	if err := runnerResult(ctx, run); err != nil {
		t.Fatal(err)
	}
	run.Spec.Pipeline.Name = "unrelated"
	if runnerResult(ctx, run) == nil {
		t.Fatal("noncatalog run read")
	}
	run.Spec.Pipeline.Name = "ranking-smoke-private"
	if runnerResult(ctx, run) == nil {
		t.Fatal("catalog-prefix lookalike read")
	}
	run.Spec.Pipeline.Name = "ranking-smoke"
	run.Spec.Pipeline.Namespace = "other"
	if runnerResult(ctx, run) == nil {
		t.Fatal("cross-project run read")
	}
}
func TestRunnerServingOnlyChangesSuspension(t *testing.T) {
	h := &apiHandler{}
	ctx := runtimeContext()
	target := &v2.InferenceServer{ObjectMeta: metav1.ObjectMeta{Name: "ranking-smoke-cpu", Namespace: "oci-ranking-showcase", UID: "original"}, Spec: v2.InferenceServerSpec{Suspended: true}}
	next := target.DeepCopy()
	next.Spec.Suspended = false
	if err := h.validateRunnerMutation(ctx, next, target); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(client.Object){
		func(x client.Object) { x.SetUID("forged") }, func(x client.Object) { x.SetFinalizers([]string{"evil"}) },
		func(x client.Object) { x.SetLabels(map[string]string{"fake": "label"}) }, func(x client.Object) { x.SetOwnerReferences([]metav1.OwnerReference{{Name: "evil"}}) },
		func(x client.Object) { x.SetAnnotations(map[string]string{"other": "changed"}) },
	} {
		next = target.DeepCopy()
		change(next)
		if h.validateRunnerMutation(ctx, next, target) == nil {
			t.Fatal("protected metadata mutation")
		}
	}
	next = target.DeepCopy()
	next.Spec.InitSpec = &v2.InitSpec{NumInstances: 99}
	if h.validateRunnerMutation(ctx, next, target) == nil {
		t.Fatal("replica mutation")
	}
	if h.validateRunnerMutation(ctx, target, nil) == nil {
		t.Fatal("runner recreated serving target")
	}
}
func TestRunnerModelAndReportCannotBypassPublication(t *testing.T) {
	h := &apiHandler{}
	ctx := runtimeContext()
	meta := metav1.ObjectMeta{Name: "ranking-smoke-0123456789abcdef", Namespace: "oci-ranking-showcase"}
	model := &v2.Model{ObjectMeta: meta}
	if h.validateRunnerMutation(ctx, model, nil) == nil {
		t.Fatal("missing provenance accepted")
	}
	if h.validateRunnerMutation(ctx, model, model.DeepCopy()) == nil {
		t.Fatal("runner updated model")
	}
	report := &v2.EvaluationReport{ObjectMeta: metav1.ObjectMeta{Name: meta.Name + "-evaluation", Namespace: meta.Namespace}}
	if h.validateRunnerMutation(ctx, report, nil) == nil {
		t.Fatal("unsealed report accepted")
	}
	family := &v2.ModelFamily{ObjectMeta: metav1.ObjectMeta{Name: "ranking-smoke", Namespace: meta.Namespace}, Spec: v2.ModelFamilySpec{Name: "ranking-smoke"}}
	if err := h.validateRunnerMutation(ctx, family, nil); err != nil {
		t.Fatal(err)
	}
	family.Namespace = "other"
	if h.validateRunnerMutation(ctx, family, nil) == nil {
		t.Fatal("cross-project family")
	}
}
