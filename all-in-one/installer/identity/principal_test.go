package auth

import (
	"context"
	"encoding/base64"
	"fmt"
	"go.uber.org/yarpc/api/transport"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestUserIdentityEveryRequest(t *testing.T) {
	enabled := true
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "POST" || r.Header.Get("Authorization") != "Bearer verified-token" {
			w.WriteHeader(401)
			return
		}
		if !enabled {
			w.WriteHeader(403)
			return
		}
		fmt.Fprint(w, `{"id":"user-1","domain_id":"domain-1","subject":"subject-1","email":"ryan@example.com","role":"admin","enabled":true}`)
	}))
	defer server.Close()
	m := &IdentityMiddleware{URL: server.URL, Client: server.Client()}
	for _, header := range []string{"", "Basic forged", "Bearer forged", "Bearer cross-audience"} {
		if _, e := m.Authenticate(context.Background(), header); status.Code(e) != codes.Unauthenticated {
			t.Fatalf("invalid identity accepted: %q %v", header, e)
		}
	}
	p, e := m.Authenticate(context.Background(), "Bearer verified-token")
	if e != nil || p.Subject != "subject-1" {
		t.Fatalf("verified principal missing: %v", e)
	}
	enabled = false
	if _, e = m.Authenticate(context.Background(), "Bearer verified-token"); status.Code(e) != codes.PermissionDenied {
		t.Fatalf("disabled user retained access: %v", e)
	}
	if calls != 4 {
		t.Fatalf("unexpected live directory calls %d", calls)
	}
}
func serviceToken(subject string) string {
	return "x." + base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf(`{"iss":"https://kubernetes.default.svc","sub":%q}`, subject))) + ".x"
}
func TestWorkloadTokenRequiresReviewAndExactAccount(t *testing.T) {
	verified := false
	subject := "system:serviceaccount:ma-system:michelangelo"
	m := &IdentityMiddleware{Review: func(context.Context, string) (string, bool, error) { return subject, verified, nil }}
	token := serviceToken(subject)
	if _, e := m.Authenticate(context.Background(), "Bearer "+token); status.Code(e) != codes.Unauthenticated {
		t.Fatal("unverified service token accepted")
	}
	verified = true
	subject = "system:serviceaccount:default:default"
	if _, e := m.Authenticate(context.Background(), "Bearer "+token); status.Code(e) != codes.PermissionDenied {
		t.Fatal("unapproved account accepted")
	}
	subject = "system:serviceaccount:ma-system:michelangelo"
	if p, e := m.Authenticate(context.Background(), "Bearer "+token); e != nil || !p.Service {
		t.Fatalf("trusted service rejected %v", e)
	}
}

type identityHandler struct {
	called  bool
	testing *testing.T
}

func (h *identityHandler) Handle(ctx context.Context, req *transport.Request, w transport.ResponseWriter) error {
	h.called = true
	for _, header := range []string{"authorization", "cookie", "x-user-name", "x-user-email"} {
		if _, ok := req.Headers.Get(header); ok {
			h.testing.Fatal("credential or asserted identity reached handler")
		}
	}
	for name := range req.Headers.OriginalItems() {
		if name == "Authorization" || name == "X-User-Name" || name == "Cookie" {
			h.testing.Fatal("original-case credential retained")
		}
	}
	if _, ok := GetPrincipal(ctx); !ok {
		h.testing.Fatal("principal absent")
	}
	return nil
}
func TestAllRPCMiddlewareAndHeaderStripping(t *testing.T) {
	m := &IdentityMiddleware{Review: func(context.Context, string) (string, bool, error) {
		return "system:serviceaccount:ma-system:ma-installer", true, nil
	}}
	for _, procedure := range []string{"GetModel", "ListProjects", "CreatePipeline", "DeleteModel"} {
		h := &identityHandler{testing: t}
		req := &transport.Request{Procedure: procedure}
		if e := m.Handle(context.Background(), req, nil, h); status.Code(e) != codes.Unauthenticated || h.called {
			t.Fatalf("unauthenticated %s accepted", procedure)
		}
		req.Headers = transport.NewHeaders().With("Authorization", "Bearer "+serviceToken("system:serviceaccount:ma-system:ma-installer")).With("X-User-Name", "spoof").With("x-user-email", "spoof").With("Cookie", "session")
		if e := m.Handle(context.Background(), req, nil, h); e != nil || !h.called {
			t.Fatalf("verified %s rejected %v", procedure, e)
		}
	}
}

type testUser struct {
	Name      string
	ProxyUser string
}
type testSpec struct {
	Owner *testUser
	Actor *testUser
	User  *testUser
}
type testObject struct {
	metav1.ObjectMeta
	Spec testSpec
}

func TestServerAttributionOverridesSpoofAndPreservesOwner(t *testing.T) {
	p := Principal{ID: "u1", DomainID: "d1", Subject: "s1", Email: "real@example.com", Enabled: true, Role: "operator"}
	ctx := WithPrincipal(context.Background(), p)
	obj := &testObject{Spec: testSpec{Owner: &testUser{Name: "spoof", ProxyUser: "victim"}, Actor: &testUser{Name: "spoof"}, User: &testUser{Name: "spoof"}}}
	if e := StampIdentity(ctx, obj, true); e != nil {
		t.Fatal(e)
	}
	if obj.Spec.Owner.Name != p.Email || obj.Spec.Actor.Name != p.Email || obj.Spec.User.Name != p.Email || obj.Spec.Owner.ProxyUser != "" {
		t.Fatal("client impersonation was retained")
	}
	if obj.Annotations["identity.michelangelo.ai/actor-subject"] != "s1" {
		t.Fatal("durable subject missing")
	}
	original := &testObject{Spec: testSpec{Owner: &testUser{Name: "original@example.com"}}}
	obj.Spec.Owner = &testUser{Name: "spoof"}
	PreserveOwner(obj, original)
	StampIdentity(ctx, obj, false)
	if obj.Spec.Owner.Name != "original@example.com" {
		t.Fatal("update forged owner")
	}
	if e := StampIdentity(context.Background(), obj, true); status.Code(e) != codes.Unauthenticated {
		t.Fatal("missing principal accepted")
	}
}

type testProjectOwner struct {
	OwningTeam  string
	Owners      []string
	OwnerGroups []string
}
type testProject struct {
	metav1.ObjectMeta
	Spec struct{ Owner *testProjectOwner }
}

func TestProjectOwnerAndImmutableCreator(t *testing.T) {
	p := Principal{ID: "real-id", Subject: "real-sub", DomainID: "domain", Email: "real@example.com", Role: "admin", Enabled: true}
	ctx := WithPrincipal(context.Background(), p)
	project := &testProject{}
	project.Spec.Owner = &testProjectOwner{OwningTeam: "team", Owners: []string{"forged@example.com"}, OwnerGroups: []string{"descriptive-group"}}
	project.SetAnnotations(map[string]string{"identity.michelangelo.ai/creator-id": "forged", "identity.michelangelo.ai/actor-id": "forged"})
	if e := StampIdentity(ctx, project, true); e != nil {
		t.Fatal(e)
	}
	if project.Spec.Owner.OwningTeam != "team" || len(project.Spec.Owner.Owners) != 1 || project.Spec.Owner.Owners[0] != p.Email {
		t.Fatal("project ownership schema corrupted or spoof retained")
	}
	original := &testObject{Spec: testSpec{Owner: &testUser{Name: "creator@example.com"}, Actor: &testUser{Name: "initiator@example.com"}}}
	original.SetAnnotations(map[string]string{"identity.michelangelo.ai/creator-id": "original-id"})
	update := &testObject{Spec: testSpec{Owner: &testUser{Name: "forged"}, Actor: &testUser{Name: "forged"}}}
	update.SetAnnotations(map[string]string{"identity.michelangelo.ai/creator-id": "forged", "identity.michelangelo.ai/unknown": "forged"})
	PreserveOwner(update, original)
	StampIdentity(ctx, update, false)
	if update.Spec.Actor.Name != "initiator@example.com" || update.Annotations["identity.michelangelo.ai/creator-id"] != "original-id" || update.Annotations["identity.michelangelo.ai/actor-id"] != p.ID {
		t.Fatal("immutable provenance or last modifier identity incorrect")
	}
	if _, ok := update.Annotations["identity.michelangelo.ai/unknown"]; ok {
		t.Fatal("forged identity annotation retained")
	}
	service := Principal{ID: "service-id", Subject: "service", Role: "operator", Enabled: true, Service: true}
	StampIdentity(WithPrincipal(context.Background(), service), update, true)
	if update.Spec.Actor.Name != "initiator@example.com" || update.Annotations["identity.michelangelo.ai/actor-kind"] != "service" {
		t.Fatal("service lost upstream human lineage or was misattributed")
	}
}
