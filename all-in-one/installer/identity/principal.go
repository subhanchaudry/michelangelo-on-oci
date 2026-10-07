package auth

import (
	"context"
	"go.uber.org/fx"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"reflect"
	"strings"
)

// Principal contains verified public identity only, never a bearer credential.
type Principal struct {
	ID       string `json:"id"`
	DomainID string `json:"domain_id"`
	Subject  string `json:"subject"`
	Email    string `json:"email"`
	Name     string `json:"name"`
	Role     string `json:"role"`
	Enabled  bool   `json:"enabled"`
	Service  bool   `json:"-"`
}
type principalKey struct{}

func WithPrincipal(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, principalKey{}, p)
}
func GetPrincipal(ctx context.Context) (Principal, bool) {
	p, ok := ctx.Value(principalKey{}).(Principal)
	return p, ok && p.Enabled
}

type PrincipalAuth struct{}

func (PrincipalAuth) UserAuthenticated(ctx context.Context) (bool, error) {
	if _, ok := GetPrincipal(ctx); !ok {
		return false, status.Error(codes.Unauthenticated, "verified identity required")
	}
	return true, nil
}
func (a PrincipalAuth) UserAuthorized(ctx context.Context, _ string, action Action, resource string) (bool, error) {
	p, ok := GetPrincipal(ctx)
	if !ok {
		return false, status.Error(codes.Unauthenticated, "verified identity required")
	}
    if p.Service && p.Subject == "system:serviceaccount:ma-system:ma-publication-controller" {
        if action == Get || action == List || (action == Update && (resource == "Model" || resource == "model" || resource == "models")) { return true, nil }
        return false, status.Error(codes.PermissionDenied, "publication service is read/finalize only")
    }
	if p.Role != "admin" && p.Role != "operator" {
		return false, status.Error(codes.PermissionDenied, "application operator access required")
	}
	return true, nil
}

var PrincipalAuthModule = fx.Options(fx.Provide(func() Auth { return PrincipalAuth{} }))

func specField(obj any, field string) reflect.Value {
	v := reflect.ValueOf(obj)
	if v.Kind() != reflect.Pointer || v.IsNil() {
		return reflect.Value{}
	}
	v = v.Elem()
	if v.Kind() != reflect.Struct {
		return reflect.Value{}
	}
	s := v.FieldByName("Spec")
	if s.Kind() == reflect.Pointer {
		if s.IsNil() {
			return reflect.Value{}
		}
		s = s.Elem()
	}
	if s.Kind() != reflect.Struct {
		return reflect.Value{}
	}
	return s.FieldByName(field)
}
func stampUser(obj any, field, email string) {
	f := specField(obj, field)
	if !f.IsValid() || !f.CanSet() || f.Kind() != reflect.Pointer || f.Type().Elem().Kind() != reflect.Struct {
		return
	}
	typ := f.Type().Elem()
	if name, ok := typ.FieldByName("Name"); ok && name.Type.Kind() == reflect.String {
		f.Set(reflect.New(typ))
		f.Elem().FieldByName("Name").SetString(email)
		return
	}
	// Project ownership has a different schema; preserve descriptive team fields.
	if owners, ok := typ.FieldByName("Owners"); ok && owners.Type == reflect.TypeOf([]string{}) {
		if f.IsNil() {
			f.Set(reflect.New(typ))
		}
		f.Elem().FieldByName("Owners").Set(reflect.ValueOf([]string{email}))
	}
	// ProxyUser is deliberately cleared: a client cannot impersonate another person.
}

// PreserveOwner prevents update payloads from forging ownership.
func PreserveOwner(dst, original any) {
	for _, field := range []string{"Owner", "Actor", "User"} {
		d := specField(dst, field)
		s := specField(original, field)
		if d.IsValid() && s.IsValid() && d.CanSet() && d.Type() == s.Type() {
			d.Set(s)
		}
	}
	// Only server-stored attribution can survive an update.
	if target, ok := dst.(metav1.Object); ok {
		if existing, ok := original.(metav1.Object); ok {
			annotations := target.GetAnnotations()
			if annotations == nil {
				annotations = map[string]string{}
			}
			for k := range annotations {
				if strings.HasPrefix(k, "identity.michelangelo.ai/") {
					delete(annotations, k)
				}
			}
			for k, v := range existing.GetAnnotations() {
				if strings.HasPrefix(k, "identity.michelangelo.ai/") {
					annotations[k] = v
				}
			}
			target.SetAnnotations(annotations)
		}
	}
}
func StampIdentity(ctx context.Context, obj metav1.Object, create bool) error {
	p, ok := GetPrincipal(ctx)
	if !ok {
		return status.Error(codes.Unauthenticated, "verified identity required")
	}
	annotations := obj.GetAnnotations()
	if annotations == nil {
		annotations = map[string]string{}
	}
	for k := range annotations {
		if strings.HasPrefix(k, "identity.michelangelo.ai/actor-") || (create && strings.HasPrefix(k, "identity.michelangelo.ai/")) {
			delete(annotations, k)
		}
	}
	annotations["identity.michelangelo.ai/actor-id"] = p.ID
	annotations["identity.michelangelo.ai/actor-subject"] = p.Subject
	annotations["identity.michelangelo.ai/actor-domain"] = p.DomainID
	annotations["identity.michelangelo.ai/actor-email"] = p.Email
	if create {
		annotations["identity.michelangelo.ai/creator-id"] = p.ID
		annotations["identity.michelangelo.ai/creator-subject"] = p.Subject
		annotations["identity.michelangelo.ai/creator-domain"] = p.DomainID
		annotations["identity.michelangelo.ai/creator-email"] = p.Email
	}
	if p.Service {
		annotations["identity.michelangelo.ai/actor-kind"] = "service"
	} else {
		annotations["identity.michelangelo.ai/actor-kind"] = "user"
		if create {
			stampUser(obj, "Actor", p.Email)
			stampUser(obj, "User", p.Email)
			stampUser(obj, "Owner", p.Email)
		}
	}
	obj.SetAnnotations(annotations)
	return nil
}
