package handler

import (
	"context"
	"github.com/michelangelo-ai/michelangelo/go/auth"
	v2 "github.com/michelangelo-ai/michelangelo/proto-go/api/v2"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"reflect"
	"regexp"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"strings"
)

var runnerCatalog = map[string]string{
	"ranking-smoke": "oci-ranking-showcase", "ranking-mlops": "oci-ranking-showcase", "ranking-advanced": "oci-ranking-showcase",
	"movielens-cf": "oci-movielens-showcase", "fraud-anomaly": "oci-fraud-anomaly-showcase",
	"housing-synthetic": "oci-california-housing-showcase", "california-housing-xgb": "oci-california-housing-showcase", "bert-cola-finetuning": "oci-bert-cola-showcase",
}

func isRunner(ctx context.Context) bool {
	p, ok := auth.GetPrincipal(ctx)
	return ok && p.Service && p.Subject == "system:serviceaccount:default:michelangelo-lifecycle-runner"
}
func runnerDenied() error {
	return status.Error(codes.PermissionDenied, "runtime identity is restricted to catalog lifecycle operations")
}
func denyRunner(ctx context.Context) error {
	if isRunner(ctx) {
		return runnerDenied()
	}
	return nil
}
func catalogDemo(namespace, name, suffix string) string {
	for demo, project := range runnerCatalog {
		if namespace == project && name == demo+suffix {
			return demo
		}
	}
	return ""
}
func candidateDemo(namespace, name, suffix string) string {
	for demo, project := range runnerCatalog {
		if namespace == project && regexp.MustCompile("^"+regexp.QuoteMeta(demo)+"-[0-9a-f]{16}"+regexp.QuoteMeta(suffix)+"$").MatchString(name) {
			return demo
		}
	}
	return ""
}
func runnerRead(ctx context.Context, namespace, name string, obj client.Object) error {
	if !isRunner(ctx) {
		return nil
	}
	allowed := false
	switch obj.(type) {
	case *v2.Model:
		allowed = candidateDemo(namespace, name, "") != ""
	case *v2.EvaluationReport:
		allowed = candidateDemo(namespace, name, "-evaluation") != ""
	case *v2.ModelFamily:
		allowed = catalogDemo(namespace, name, "") != ""
	case *v2.InferenceServer:
		allowed = catalogDemo(namespace, name, "-cpu") != ""
	case *v2.Deployment:
		allowed = catalogDemo(namespace, name, "-deployment") != ""
	case *v2.PipelineRun:
		for _, project := range runnerCatalog {
			if namespace == project && name != "" {
				allowed = true
			}
		}
	}
	if !allowed {
		return runnerDenied()
	}
	return nil
}
func runnerResult(ctx context.Context, obj client.Object) error {
	if !isRunner(ctx) {
		return nil
	}
	if err := runnerRead(ctx, obj.GetNamespace(), obj.GetName(), obj); err != nil {
		return err
	}
	switch value := obj.(type) {
	case *v2.Model:
		demo := candidateDemo(value.Namespace, value.Name, "")
		if value.Spec.ModelFamily == nil || value.Spec.ModelFamily.Name != demo || value.Spec.ModelFamily.Namespace != value.Namespace || value.Annotations["michelangelo.ai/demo"] != demo {
			return runnerDenied()
		}
	case *v2.PipelineRun:
		ref := value.Spec.Pipeline
		if ref == nil || (ref.Namespace != "" && ref.Namespace != value.Namespace) {
			return runnerDenied()
		}
		if runnerPipelines[value.Namespace][ref.Name] == "" {
			return runnerDenied()
		}
	}
	return nil
}
func unchangedRunnerMetadata(obj, original client.Object) bool {
	if obj.GetUID() != original.GetUID() || !reflect.DeepEqual(obj.GetLabels(), original.GetLabels()) ||
		!reflect.DeepEqual(obj.GetOwnerReferences(), original.GetOwnerReferences()) || !reflect.DeepEqual(obj.GetFinalizers(), original.GetFinalizers()) ||
		!reflect.DeepEqual(obj.GetDeletionTimestamp(), original.GetDeletionTimestamp()) || !reflect.DeepEqual(obj.GetDeletionGracePeriodSeconds(), original.GetDeletionGracePeriodSeconds()) {
		return false
	}
	clean := func(values map[string]string) map[string]string {
		result := map[string]string{}
		for key, value := range values {
			if key != "michelangelo.ai/operator-revisions" && key != "michelangelo.ai/last-operator-run" {
				result[key] = value
			}
		}
		return result
	}
	return reflect.DeepEqual(clean(obj.GetAnnotations()), clean(original.GetAnnotations()))
}
func (handler *apiHandler) validateRunnerMutation(ctx context.Context, obj, original client.Object) error {
	if !isRunner(ctx) {
		return nil
	}
	if err := runnerRead(ctx, obj.GetNamespace(), obj.GetName(), obj); err != nil {
		return err
	}
	namespace := obj.GetNamespace()
	if original == nil {
		if len(obj.GetOwnerReferences()) != 0 || len(obj.GetFinalizers()) != 0 || obj.GetUID() != "" {
			return runnerDenied()
		}
		switch value := obj.(type) {
		case *v2.Model:
			demo := candidateDemo(namespace, value.Name, "")
			source := value.Spec.SourcePipelineRun
			report := value.Spec.PerformanceEvaluationReport
			family := value.Spec.ModelFamily
			if source == nil || source.Name == "" || source.Namespace != namespace || report == nil || report.Namespace != namespace || report.Name != value.Name+"-evaluation" || family == nil || family.Namespace != namespace || family.Name != demo || value.Annotations["michelangelo.ai/demo"] != demo || value.Labels["michelangelo.ai/eligible"] == "true" {
				return runnerDenied()
			}
			for key, val := range value.Annotations {
				if strings.HasPrefix(key, "michelangelo.ai/publication-") && (key != "michelangelo.ai/publication-state" || val != "pending") {
					return runnerDenied()
				}
			}
			run := &v2.PipelineRun{}
			if err := handler.Get(ctx, namespace, source.Name, &metav1.GetOptions{}, run); err != nil {
				return err
			}
			if run.Spec.Pipeline == nil || runnerPipelines[namespace][run.Spec.Pipeline.Name] != demo {
				return runnerDenied()
			}
			evaluation := &v2.EvaluationReport{}
			if err := handler.Get(ctx, namespace, report.Name, &metav1.GetOptions{}, evaluation); err != nil {
				return err
			}
			if evaluation.Spec.SourcePipelineRun == nil || !reflect.DeepEqual(evaluation.Spec.SourcePipelineRun, source) {
				return runnerDenied()
			}
		case *v2.EvaluationReport:
			if !value.Spec.Sealed || value.Spec.SourcePipelineRun == nil || value.Spec.SourcePipelineRun.Name == "" || value.Spec.SourcePipelineRun.Namespace != namespace {
				return runnerDenied()
			}
			run := &v2.PipelineRun{}
			if err := handler.Get(ctx, namespace, value.Spec.SourcePipelineRun.Name, &metav1.GetOptions{}, run); err != nil {
				return err
			}
			if run.Spec.Pipeline == nil || runnerPipelines[namespace][run.Spec.Pipeline.Name] != candidateDemo(namespace, value.Name, "-evaluation") {
				return runnerDenied()
			}
		case *v2.ModelFamily:
			if value.Spec.Name != value.Name {
				return runnerDenied()
			}
		default:
			return runnerDenied()
		}
		return nil
	}
	if !unchangedRunnerMetadata(obj, original) {
		return runnerDenied()
	}
	switch value := obj.(type) {
	case *v2.InferenceServer:
		old, ok := original.(*v2.InferenceServer)
		if !ok {
			return runnerDenied()
		}
		copy := old.DeepCopy()
		copy.Spec.Suspended = value.Spec.Suspended
		if !reflect.DeepEqual(copy.Spec, value.Spec) {
			return runnerDenied()
		}
	case *v2.Deployment:
		old, ok := original.(*v2.Deployment)
		if !ok {
			return runnerDenied()
		}
		copy := old.DeepCopy()
		demo := catalogDemo(namespace, value.Name, "-deployment")
		if value.Spec.GetInferenceServer() == nil || value.Spec.GetInferenceServer().Name != demo+"-cpu" || value.Spec.GetInferenceServer().Namespace != namespace || value.Spec.ModelFamily == nil || value.Spec.ModelFamily.Name != demo || value.Spec.ModelFamily.Namespace != namespace {
			return runnerDenied()
		}
		if value.Spec.DesiredRevision == nil || value.Spec.DesiredRevision.Namespace != namespace || candidateDemo(namespace, value.Spec.DesiredRevision.Name, "") != demo {
			return runnerDenied()
		}
		model := &v2.Model{}
		if err := handler.Get(ctx, namespace, value.Spec.DesiredRevision.Name, &metav1.GetOptions{}, model); err != nil {
			return err
		}
		copy.Spec.DesiredRevision = value.Spec.DesiredRevision
		if copy.Spec.Definition != nil && value.Spec.Definition != nil && value.Spec.Definition.SubType == "realtime-serving" {
			copy.Spec.Definition.SubType = value.Spec.Definition.SubType
		}
		if !reflect.DeepEqual(copy.Spec, value.Spec) {
			return runnerDenied()
		}
	default:
		return runnerDenied()
	}
	return nil
}
