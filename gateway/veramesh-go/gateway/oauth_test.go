package gateway

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/auth"
)

func verifierConfig(endpoint string) IntrospectionConfig {
	return IntrospectionConfig{
		Schema:           "VERAMESH_OAUTH_INTROSPECTION_V1",
		Endpoint:         endpoint,
		ClientID:         "veramesh-resource",
		ClientSecretEnv:  "VERAMESH_TEST_OAUTH_SECRET",
		ExpectedIssuer:   "https://issuer.example",
		ExpectedResource: "https://mesh.example/mcp",
		ClientAuthMethod: "client_secret_basic",
		TimeoutS:         2,
	}
}

func validClaims() map[string]any {
	return map[string]any{
		"active":    true,
		"iss":       "https://issuer.example",
		"sub":       "patrick",
		"client_id": "chatgpt",
		"scope":     "computer.profile computer.read",
		"exp":       float64(2000),
		"aud":       "https://mesh.example/mcp",
	}
}

func newVerifierServer(t *testing.T, claims map[string]any, status int) (*IntrospectionVerifier, *httptest.Server) {
	t.Helper()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, pass, ok := r.BasicAuth()
		if !ok || user != "veramesh-resource" || pass != "secret" {
			http.Error(w, "bad auth", http.StatusUnauthorized)
			return
		}
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad form", http.StatusBadRequest)
			return
		}
		if r.Form.Get("token") != "opaque-token" {
			http.Error(w, "bad token", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		if status == http.StatusOK {
			json.NewEncoder(w).Encode(claims)
		}
	}))
	cfg := verifierConfig(server.URL)
	verifier, err := NewIntrospectionVerifier(cfg)
	if err != nil {
		server.Close()
		t.Fatal(err)
	}
	verifier.client = server.Client()
	verifier.client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse
	}
	verifier.now = func() time.Time { return time.Unix(1000, 0) }
	t.Setenv("VERAMESH_TEST_OAUTH_SECRET", "secret")
	return verifier, server
}

func TestIntrospectionVerifierAcceptsBoundResourceOwner(t *testing.T) {
	verifier, server := newVerifierServer(t, validClaims(), http.StatusOK)
	defer server.Close()

	info, err := verifier.Verify(t.Context(), "opaque-token", nil)
	if err != nil {
		t.Fatal(err)
	}
	if info.UserID != "patrick" {
		t.Fatalf("wrong subject: %q", info.UserID)
	}
	if info.Expiration.Unix() != 2000 {
		t.Fatalf("wrong expiration: %v", info.Expiration)
	}
	if strings.Join(info.Scopes, " ") != "computer.profile computer.read" {
		t.Fatalf("wrong scopes: %#v", info.Scopes)
	}
}

func TestIntrospectionVerifierFailsClosedOnIdentityResourceAndExpiry(t *testing.T) {
	cases := map[string]func(map[string]any){
		"inactive": func(v map[string]any) { v["active"] = false },
		"issuer": func(v map[string]any) { v["iss"] = "https://evil.example" },
		"subject": func(v map[string]any) { v["sub"] = "" },
		"resource": func(v map[string]any) { v["aud"] = "https://other.example/mcp" },
		"expiry": func(v map[string]any) { v["exp"] = float64(999) },
		"scope": func(v map[string]any) { delete(v, "scope") },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			claims := validClaims()
			mutate(claims)
			verifier, server := newVerifierServer(t, claims, http.StatusOK)
			defer server.Close()
			if info, err := verifier.Verify(t.Context(), "opaque-token", nil); err == nil || info != nil {
				t.Fatalf("invalid token accepted: info=%#v err=%v", info, err)
			}
		})
	}
}

func TestIntrospectionVerifierAcceptsResourceFieldOrAudienceList(t *testing.T) {
	for _, claims := range []map[string]any{
		func() map[string]any {
			v := validClaims()
			delete(v, "aud")
			v["resource"] = "https://mesh.example/mcp"
			return v
		}(),
		func() map[string]any {
			v := validClaims()
			v["aud"] = []any{"other", "https://mesh.example/mcp"}
			return v
		}(),
	} {
		verifier, server := newVerifierServer(t, claims, http.StatusOK)
		info, err := verifier.Verify(t.Context(), "opaque-token", nil)
		server.Close()
		if err != nil || info == nil {
			t.Fatalf("valid resource binding rejected: info=%#v err=%v", info, err)
		}
	}
}

func TestIntrospectionVerifierRejectsMissingSecretBeforeNetwork(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("network should not be called without client secret")
	}))
	defer server.Close()
	cfg := verifierConfig(server.URL)
	verifier, err := NewIntrospectionVerifier(cfg)
	if err != nil {
		t.Fatal(err)
	}
	verifier.client = server.Client()
	os.Unsetenv("VERAMESH_TEST_OAUTH_SECRET")
	if info, err := verifier.Verify(t.Context(), "opaque-token", nil); err == nil || info != nil {
		t.Fatalf("missing secret accepted: info=%#v err=%v", info, err)
	}
}

func TestIntrospectionVerifierRejectsNon200AndRedirect(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusFound} {
		verifier, server := newVerifierServer(t, validClaims(), status)
		info, err := verifier.Verify(t.Context(), "opaque-token", nil)
		server.Close()
		if err == nil || info != nil {
			t.Fatalf("HTTP %d accepted: info=%#v err=%v", status, info, err)
		}
	}
}

func TestIntrospectionConfigRejectsHTTPAndUnknownClientAuth(t *testing.T) {
	cfg := verifierConfig("http://login.example/introspect")
	if _, err := NewIntrospectionVerifier(cfg); err == nil {
		t.Fatal("HTTP introspection endpoint accepted")
	}
	cfg = verifierConfig("https://login.example/introspect")
	cfg.ClientAuthMethod = "private_key_jwt"
	if _, err := NewIntrospectionVerifier(cfg); err == nil {
		t.Fatal("unsupported client auth method accepted")
	}
}

func TestVerifierErrorsWrapInvalidToken(t *testing.T) {
	claims := validClaims()
	claims["active"] = false
	verifier, server := newVerifierServer(t, claims, http.StatusOK)
	defer server.Close()
	_, err := verifier.Verify(t.Context(), "opaque-token", nil)
	if err == nil || !errors.Is(err, auth.ErrInvalidToken) {
		t.Fatalf("error does not wrap auth.ErrInvalidToken: %v", err)
	}
}
