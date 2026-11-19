package auth

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"go.uber.org/yarpc/api/transport"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"io"
	authenticationv1 "k8s.io/api/authentication/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type IdentityMiddleware struct {
	URL    string
	Client *http.Client
	Review func(context.Context, string) (string, bool, error)
}

func NewIdentityMiddleware() *IdentityMiddleware {
	url := os.Getenv("MA_IDENTITY_RESOLVE_URL")
	if url == "" {
		url = "http://ma-identity:8081/internal/resolve"
	}
	m := &IdentityMiddleware{URL: url, Client: &http.Client{Timeout: 8 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	// Lazy initialization makes configuration errors fail requests closed, not silently disable auth.
	var once sync.Once
	var client kubernetes.Interface
	var err error
	m.Review = func(ctx context.Context, token string) (string, bool, error) {
		once.Do(func() {
			var cfg *rest.Config
			cfg, err = rest.InClusterConfig()
			if err == nil {
				client, err = kubernetes.NewForConfig(cfg)
			}
		})
		if err != nil {
			return "", false, err
		}
		review, e := client.AuthenticationV1().TokenReviews().Create(ctx, &authenticationv1.TokenReview{Spec: authenticationv1.TokenReviewSpec{Token: token}}, metav1.CreateOptions{})
		if e != nil {
			return "", false, e
		}
		return review.Status.User.Username, review.Status.Authenticated, nil
	}
	return m
}

var trustedServices = map[string]bool{
 "system:serviceaccount:ma-system:ma-publication-controller": true,
	"system:serviceaccount:ma-system:michelangelo":                true,
	"system:serviceaccount:ma-system:ma-installer":                true,
	"system:serviceaccount:default:michelangelo-lifecycle-runner": true,
}

// This unverified issuer is only a routing hint. Both paths perform cryptographic verification.
func kubernetesIssuer(token string) bool {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return false
	}
	b, e := base64.RawURLEncoding.DecodeString(parts[1])
	if e != nil {
		return false
	}
	var claims struct {
		Issuer  string `json:"iss"`
		Subject string `json:"sub"`
	}
	if json.Unmarshal(b, &claims) != nil {
		return false
	}
	return strings.HasPrefix(claims.Subject, "system:serviceaccount:") && claims.Issuer != "https://identity.oraclecloud.com/"
}
func (m *IdentityMiddleware) Authenticate(ctx context.Context, header string) (OCIPrincipal, error) {
	fields := strings.Fields(header)
	if len(fields) != 2 || !strings.EqualFold(fields[0], "Bearer") || len(fields[1]) > 16384 {
		return OCIPrincipal{}, status.Error(codes.Unauthenticated, "bearer identity required")
	}
	token := fields[1]
	if kubernetesIssuer(token) {
		if m.Review == nil {
			return OCIPrincipal{}, status.Error(codes.Unauthenticated, "workload verification unavailable")
		}
		user, verified, e := m.Review(ctx, token)
		if e != nil {
			return OCIPrincipal{}, status.Error(codes.Unavailable, "workload verification unavailable")
		}
		if !verified {
			return OCIPrincipal{}, status.Error(codes.Unauthenticated, "invalid workload identity")
		}
		if !trustedServices[user] {
			return OCIPrincipal{}, status.Error(codes.PermissionDenied, "workload identity is not authorized")
		}
		return OCIPrincipal{ID: user, Subject: user, Name: user, Role: "operator", Enabled: true, Service: true}, nil
	}
	request, e := http.NewRequestWithContext(ctx, http.MethodPost, m.URL, nil)
	if e != nil {
		return OCIPrincipal{}, status.Error(codes.Unavailable, "identity service unavailable")
	}
	request.Header.Set("Authorization", "Bearer "+token)
	response, e := m.Client.Do(request)
	if e != nil {
		return OCIPrincipal{}, status.Error(codes.Unavailable, "identity service unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode == http.StatusForbidden {
		return OCIPrincipal{}, status.Error(codes.PermissionDenied, "user is not enabled for this application")
	}
	if response.StatusCode == http.StatusUnauthorized {
		return OCIPrincipal{}, status.Error(codes.Unauthenticated, "invalid user identity")
	}
	if response.StatusCode != http.StatusOK {
		return OCIPrincipal{}, status.Error(codes.Unavailable, "identity service unavailable")
	}
	var p OCIPrincipal
	if json.NewDecoder(io.LimitReader(response.Body, 16384)).Decode(&p) != nil || !p.Enabled || p.ID == "" || p.Subject == "" || p.DomainID == "" || p.Email == "" || (p.Role != "admin" && p.Role != "operator") {
		return OCIPrincipal{}, status.Error(codes.PermissionDenied, "invalid application identity")
	}
	return p, nil
}
func (m *IdentityMiddleware) Handle(ctx context.Context, req *transport.Request, w transport.ResponseWriter, next transport.UnaryHandler) error {
	header, _ := req.Headers.Get("authorization")
	p, e := m.Authenticate(ctx, header)
	if e != nil {
		return e
	}
	// Do not retain credentials or client asserted identity in downstream context/logs.
	for original := range req.Headers.OriginalItems() {
		for _, sensitive := range []string{"authorization", "cookie", "x-user-name", "x-user-email"} {
			if strings.EqualFold(original, sensitive) {
				req.Headers.Del(original)
			}
		}
	}
	return next.Handle(WithOCIPrincipal(ctx, p), req, w)
}

type ServiceTokenMiddleware struct{}

func (ServiceTokenMiddleware) Call(ctx context.Context, req *transport.Request, out transport.UnaryOutbound) (*transport.Response, error) {
	path := os.Getenv("MA_API_TOKEN_FILE")
	if path == "" {
		path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
	}
	b, e := os.ReadFile(path)
	if e != nil || strings.TrimSpace(string(b)) == "" {
		return nil, errors.New("workload bearer credential unavailable")
	}
	req.Headers = req.Headers.With("authorization", "Bearer "+strings.TrimSpace(string(b)))
	return out.Call(ctx, req)
}
