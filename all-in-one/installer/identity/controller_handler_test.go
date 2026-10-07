package auth

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/michelangelo-ai/michelangelo/go/api"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

type recordingControllerHandler struct {
	api.Handler
	calls      int
	principals []Principal
}

func (r *recordingControllerHandler) Create(ctx context.Context, obj client.Object, _ *metav1.CreateOptions) error {
	r.calls++
	p, _ := GetPrincipal(ctx)
	r.principals = append(r.principals, p)
	return StampIdentity(ctx, obj, true)
}
func TestControllerContextVerifiedRotatingToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	t.Setenv("MA_API_TOKEN_FILE", path)
	subject := "system:serviceaccount:ma-system:michelangelo"
	reviewed := []string{}
	inner := &recordingControllerHandler{}
	h := &localServiceHandler{Handler: inner, identity: &IdentityMiddleware{Review: func(_ context.Context, token string) (string, bool, error) {
		reviewed = append(reviewed, token)
		return subject, true, nil
	}}}
	token := serviceToken(subject)
	if err := os.WriteFile(path, []byte(token), 0600); err != nil {
		t.Fatal(err)
	}
	obj := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "owned"}}
	if err := h.Create(context.Background(), obj, &metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	rotated := token + "rotated"
	if err := os.WriteFile(path, []byte(rotated), 0600); err != nil {
		t.Fatal(err)
	}
	if err := h.Create(context.Background(), &corev1.ConfigMap{}, &metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	if len(reviewed) != 2 || reviewed[0] != token || reviewed[1] != rotated {
		t.Fatal("projected token was not re-read and reviewed")
	}
	if inner.calls != 2 || !inner.principals[0].Service || obj.Annotations["identity.michelangelo.ai/creator-id"] != subject {
		t.Fatal("verified service attribution missing")
	}
}
func TestControllerRejectsMissingInvalidOrUnapprovedCredential(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	t.Setenv("MA_API_TOKEN_FILE", path)
	subject := "system:serviceaccount:ma-system:michelangelo"
	verified := true
	inner := &recordingControllerHandler{}
	h := &localServiceHandler{Handler: inner, identity: &IdentityMiddleware{Review: func(context.Context, string) (string, bool, error) { return subject, verified, nil }}}
	if h.Create(context.Background(), &corev1.ConfigMap{}, nil) == nil {
		t.Fatal("missing credential accepted")
	}
	if err := os.WriteFile(path, []byte(serviceToken(subject)), 0600); err != nil {
		t.Fatal(err)
	}
	verified = false
	if h.Create(context.Background(), &corev1.ConfigMap{}, nil) == nil {
		t.Fatal("invalid token accepted")
	}
	verified = true
	subject = "system:serviceaccount:default:default"
	if h.Create(context.Background(), &corev1.ConfigMap{}, nil) == nil {
		t.Fatal("unapproved service accepted")
	}
	if inner.calls != 0 {
		t.Fatal("failed authentication reached mutation")
	}
}
func TestControllerPreservesVerifiedHumanContext(t *testing.T) {
	inner := &recordingControllerHandler{}
	h := &localServiceHandler{Handler: inner}
	p := Principal{ID: "trusted-user", Subject: "subject", Email: "owner@example.com", Enabled: true, Role: "operator"}
	obj := &corev1.ConfigMap{}
	if err := h.Create(WithPrincipal(context.Background(), p), obj, nil); err != nil {
		t.Fatal(err)
	}
	if inner.principals[0] != p || obj.Annotations["identity.michelangelo.ai/creator-id"] != p.ID {
		t.Fatal("verified human origin lost")
	}
}
