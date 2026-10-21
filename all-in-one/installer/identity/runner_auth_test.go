package auth

import (
	"context"
	"testing"
)

func TestRuntimeCoarseAuthorization(t *testing.T) {
	ctx := WithPrincipal(context.Background(), Principal{Enabled: true, Service: true, Role: "operator", Subject: "system:serviceaccount:default:michelangelo-lifecycle-runner"})
	for _, resource := range []string{"Model", "EvaluationReport", "ModelFamily", "PipelineRun", "InferenceServer", "Deployment", "Cluster", "Project", "RayJob", "Pipeline"} {
		for _, action := range []Action{Create, Get, Update, Delete, List, DeleteCollection} {
			want := action == Get && (resource == "Model" || resource == "EvaluationReport" || resource == "ModelFamily" || resource == "PipelineRun" || resource == "InferenceServer" || resource == "Deployment") || action == Create && (resource == "Model" || resource == "EvaluationReport" || resource == "ModelFamily") || action == Update && (resource == "InferenceServer" || resource == "Deployment")
			allowed, _ := (PrincipalAuth{}).UserAuthorized(ctx, "oci-ranking-showcase", action, resource)
			if allowed != want {
				t.Fatalf("%s %s allowed=%v want=%v", action, resource, allowed, want)
			}
			allowed, _ = (PrincipalAuth{}).UserAuthorized(ctx, "ma-system", action, resource)
			if allowed {
				t.Fatal("platform namespace access")
			}
		}
	}
}
