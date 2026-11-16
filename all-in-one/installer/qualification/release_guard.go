package handler

import (
	"context"
	"encoding/hex"
	"github.com/gogo/protobuf/proto"
	"reflect"
	"strings"

	"github.com/michelangelo-ai/michelangelo/go/auth"
	apipb "github.com/michelangelo-ai/michelangelo/proto-go/api"
	v2 "github.com/michelangelo-ai/michelangelo/proto-go/api/v2"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const publicationPrefix = "michelangelo.ai/"
const publicationService = "system:serviceaccount:ma-system:ma-publication-controller"

type releaseGet func(context.Context, string, string, *metav1.GetOptions, client.Object) error

func startsWork(obj, original client.Object) bool {
	switch value := obj.(type) {
	case *v2.PipelineRun:
		if original == nil {
			return true
		}
		old, ok := original.(*v2.PipelineRun)
		// Retry is processed before cancellation by the workflow controller.
		return !ok || !reflect.DeepEqual(value.Spec.Resume, old.Spec.Resume) || !reflect.DeepEqual(value.Spec.RetryInfo, old.Spec.RetryInfo)
	case *v2.RayJob:
		return original == nil || !reflect.DeepEqual(value.Spec, original.(*v2.RayJob).Spec)
	case *v2.SparkJob:
		if original == nil {
			return true
		}
		old := original.(*v2.SparkJob).DeepCopy()
		old.Spec.Termination = value.Spec.Termination // Allow cancellation while draining.
		return !reflect.DeepEqual(value.Spec, old.Spec)
	case *v2.TriggerRun:
		if original == nil {
			return true
		}
		old, ok := original.(*v2.TriggerRun)
		return !ok || (value.Spec.Action == v2.TRIGGER_RUN_ACTION_RESUME && old.Spec.Action != value.Spec.Action)
	case *v2.InferenceServer:
		return !value.Spec.Suspended && (original == nil || original.(*v2.InferenceServer).Spec.Suspended)
	case *v2.Deployment:
		return value.Spec.DesiredRevision != nil && (original == nil || !reflect.DeepEqual(value.Spec.DesiredRevision, original.(*v2.Deployment).Spec.DesiredRevision))
	}
	return false
}

func (handler *apiHandler) validateReleaseMutation(ctx context.Context, obj, original client.Object) error {
	if startsWork(obj, original) {
		config := &corev1.ConfigMap{}
		if err := handler.k8sHandler.Get(ctx, "ma-system", "ma-release-control", config); err != nil {
			return status.Error(codes.Unavailable, "release admission state unavailable")
		}
		principal, verified := auth.GetPrincipal(ctx)
		_, isRun := obj.(*v2.PipelineRun)
		installerProbe := verified && principal.Service && principal.Subject == "system:serviceaccount:ma-system:ma-installer" && isRun && original == nil && obj.GetAnnotations()["michelangelo.ai/qualification"] == "installer"
		_, rayJob := obj.(*v2.RayJob)
		_, sparkJob := obj.(*v2.SparkJob)
		// Only authenticated internal workers may finish already admitted workflows.
		drainingChild := verified && principal.Service && principal.Subject == "system:serviceaccount:ma-system:michelangelo" && (rayJob || sparkJob)
		if config.Data["maintenance"] != "false" && !installerProbe && !drainingChild {
			return status.Error(codes.Unavailable, "maintenance: new work is paused")
		}
	}
	if target, ok := obj.(*v2.InferenceServer); ok && !target.Spec.Suspended {
		// Resuming a target must not bypass revision admission. Read live native
		// deployments as metadata ingestion may lag the currently selected revision.
		deployments := &v2.DeploymentList{}
		if err := handler.k8sHandler.List(ctx, target.Namespace, &metav1.ListOptions{}, deployments); err != nil {
			return err
		}
		for i := range deployments.Items {
			deployment := &deployments.Items[i]
			ref := deployment.Spec.GetInferenceServer()
			if ref != nil && ref.Name == target.Name && (ref.Namespace == "" || ref.Namespace == target.Namespace) {
				if err := validatePublicationMutation(ctx, deployment, deployment, handler.Get); err != nil {
					return err
				}
				for _, revision := range []*apipb.ResourceIdentifier{deployment.Status.CurrentRevision, deployment.Status.CandidateRevision} {
					if revision == nil || revision.Name == "" {
						continue
					}
					active := deployment.DeepCopy()
					active.Spec.DesiredRevision = revision
					if err := validatePublicationMutation(ctx, active, deployment, handler.Get); err != nil {
						return err
					}
				}
			}
		}
	}
	return validatePublicationMutation(ctx, obj, original, handler.Get)
}

func validatePublicationMutation(ctx context.Context, obj, original client.Object, get releaseGet) error {
	p, verified := auth.GetPrincipal(ctx)
	finalizer := verified && p.Service && p.Subject == publicationService
	if finalizer {
		if _, ok := obj.(*v2.Model); !ok || original == nil {
			return status.Error(codes.PermissionDenied, "publication service may only finalize existing models")
		}
	}
	switch model := obj.(type) {
	case *v2.Model:
		ann := model.GetAnnotations()
		if ann == nil {
			ann = map[string]string{}
		}
		labels := model.GetLabels()
		if labels == nil {
			labels = map[string]string{}
		}
		if original == nil {
			if model.GetUID() != "" {
				return status.Error(codes.InvalidArgument, "candidate identity must be assigned by the server")
			}
			labels[publicationPrefix+"eligible"] = "false"
			for key := range ann {
				if strings.HasPrefix(key, publicationPrefix+"publication-") {
					delete(ann, key)
				}
			}
			ann[publicationPrefix+"publication-state"] = "pending"
		} else {
			old := original.(*v2.Model)
			if model.GetUID() != old.GetUID() {
				return status.Error(codes.FailedPrecondition, "registered model identity is immutable")
			}
			// A proof cannot survive changes to the candidate's immutable inputs.
			if !proto.Equal(&model.Spec, &old.Spec) {
				return status.Error(codes.FailedPrecondition, "registered model specification is immutable")
			}
			for key, value := range old.GetAnnotations() {
				if strings.HasPrefix(key, publicationPrefix) && !strings.HasPrefix(key, publicationPrefix+"publication-") && ann[key] != value {
					return status.Error(codes.FailedPrecondition, "registered model provenance is immutable")
				}
			}
			for key, value := range ann {
				if strings.HasPrefix(key, publicationPrefix) && !strings.HasPrefix(key, publicationPrefix+"publication-") && old.GetAnnotations()[key] != value {
					return status.Error(codes.FailedPrecondition, "registered model provenance is immutable")
				}
			}
			if !finalizer {
				for key := range ann {
					if strings.HasPrefix(key, publicationPrefix+"publication-") {
						delete(ann, key)
					}
				}
				for key, value := range old.GetAnnotations() {
					if strings.HasPrefix(key, publicationPrefix+"publication-") {
						ann[key] = value
					}
				}
				labels[publicationPrefix+"eligible"] = old.GetLabels()[publicationPrefix+"eligible"]
			} else {
				state := ann[publicationPrefix+"publication-state"]
				if state != "eligible" && state != "quarantined" {
					return status.Error(codes.InvalidArgument, "invalid publication transition")
				}
				prior := old.GetAnnotations()[publicationPrefix+"publication-state"]
				if (prior == "eligible" || prior == "quarantined") && prior != state {
					return status.Error(codes.FailedPrecondition, "publication is terminal")
				}
				if (state == "eligible") != (labels[publicationPrefix+"eligible"] == "true") {
					return status.Error(codes.InvalidArgument, "inconsistent publication state")
				}
				if state == "eligible" {
					proof, err := hex.DecodeString(ann[publicationPrefix+"publication-proof-sha256"])
					if err != nil || len(proof) != 32 {
						return status.Error(codes.FailedPrecondition, "publication proof required")
					}
					if err := successfulModelSource(ctx, model, get); err != nil {
						return err
					}
				}
			}
		}
		model.SetAnnotations(ann)
		model.SetLabels(labels)
	case *v2.Deployment:
		ref := model.Spec.DesiredRevision
		if ref == nil || ref.Name == "" {
			return nil
		}
		namespace := ref.Namespace
		if namespace == "" {
			namespace = model.GetNamespace()
		}
		if namespace != model.GetNamespace() {
			return status.Error(codes.FailedPrecondition, "cross-project deployment is not permitted")
		}
		candidate := &v2.Model{}
		if err := get(ctx, namespace, ref.Name, &metav1.GetOptions{}, candidate); err != nil {
			return err
		}
		if candidate.GetLabels()[publicationPrefix+"eligible"] != "true" || candidate.GetAnnotations()[publicationPrefix+"publication-state"] != "eligible" {
			return status.Error(codes.FailedPrecondition, "model publication is not eligible")
		}
		if err := successfulModelSource(ctx, candidate, get); err != nil {
			return err
		}
	}
	return nil
}

func successfulModelSource(ctx context.Context, model *v2.Model, get releaseGet) error {
	ref := model.Spec.SourcePipelineRun
	if ref == nil || ref.Name == "" || ref.Namespace != model.GetNamespace() {
		return status.Error(codes.FailedPrecondition, "model source run is missing or outside this project")
	}
	run := &v2.PipelineRun{}
	if err := get(ctx, ref.Namespace, ref.Name, &metav1.GetOptions{}, run); err != nil {
		return err
	}
	if run.Status.State != v2.PIPELINE_RUN_STATE_SUCCEEDED {
		return status.Error(codes.FailedPrecondition, "model source run did not succeed")
	}
	return nil
}
