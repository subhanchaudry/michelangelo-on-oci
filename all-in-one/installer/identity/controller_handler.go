package auth

import (
	"context"
	"errors"
	"os"
	"strings"

	"github.com/michelangelo-ai/michelangelo/go/api"
	apipb "github.com/michelangelo-ai/michelangelo/proto-go/api"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// localServiceHandler is installed only by controller handler constructors.
// Public RPC handlers still require the independently authenticated inbound
// principal; no missing-principal fallback is added to public API mutations.
type localServiceHandler struct {
	api.Handler
	identity *IdentityMiddleware
}

func WrapControllerHandler(handler api.Handler, err error) (api.Handler, error) {
	if err != nil {
		return nil, err
	}
	return &localServiceHandler{Handler: handler, identity: NewIdentityMiddleware()}, nil
}

func (h *localServiceHandler) serviceContext(ctx context.Context) (context.Context, error) {
	// A verified in-process principal is retained, including its human origin.
	if _, ok := GetPrincipal(ctx); ok {
		return ctx, nil
	}
	path := os.Getenv("MA_API_TOKEN_FILE")
	if path == "" {
		path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
	}
	token, err := os.ReadFile(path)
	if err != nil || strings.TrimSpace(string(token)) == "" {
		return nil, errors.New("controller workload credential unavailable")
	}
	// Re-read the projected rotating token and verify it on every new context.
	// Authenticate enforces TokenReview plus the unchanged exact service allowlist.
	principal, err := h.identity.Authenticate(ctx, "Bearer "+strings.TrimSpace(string(token)))
	if err != nil {
		return nil, err
	}
	if !principal.Service {
		return nil, errors.New("controller workload identity required")
	}
	return WithOCIPrincipal(ctx, principal), nil
}

func (h *localServiceHandler) Create(ctx context.Context, obj client.Object, opts *metav1.CreateOptions) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.Create(ctx, obj, opts)
}
func (h *localServiceHandler) Get(ctx context.Context, namespace, name string, opts *metav1.GetOptions, obj client.Object) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.Get(ctx, namespace, name, opts, obj)
}
func (h *localServiceHandler) Update(ctx context.Context, obj client.Object, opts *metav1.UpdateOptions) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.Update(ctx, obj, opts)
}
func (h *localServiceHandler) UpdateStatus(ctx context.Context, obj client.Object, opts *metav1.UpdateOptions) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.UpdateStatus(ctx, obj, opts)
}
func (h *localServiceHandler) Delete(ctx context.Context, obj client.Object, opts *metav1.DeleteOptions) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.Delete(ctx, obj, opts)
}
func (h *localServiceHandler) List(ctx context.Context, namespace string, opts *metav1.ListOptions, ext *apipb.ListOptionsExt, list client.ObjectList) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.List(ctx, namespace, opts, ext, list)
}
func (h *localServiceHandler) DeleteCollection(ctx context.Context, obj client.Object, namespace string, del *metav1.DeleteOptions, list *metav1.ListOptions) error {
	ctx, err := h.serviceContext(ctx)
	if err != nil {
		return err
	}
	return h.Handler.DeleteCollection(ctx, obj, namespace, del, list)
}
