package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/auth"
)

func testPublicController() *Controller {
	return &Controller{
		cfg: &Config{
			RequestedCapabilities: []string{"fs.read"},
			GatewayOperations: []string{"lane.open", "lane.close", "fs.read_text"},
		},
	}
}

func testPublicOAuthConfig() IntrospectionConfig {
	return IntrospectionConfig{
		Schema:           "VERAMESH_OAUTH_INTROSPECTION_V1",
		Endpoint:         "https://issuer.example/introspect",
		ClientID:         "veramesh-resource",
		ClientSecretEnv:  "VERAMESH_TEST_SECRET",
		ExpectedIssuer:   "https://issuer.example",
		ExpectedResource: "https://mesh.example/mcp",
		ClientAuthMethod: "client_secret_basic",
		TimeoutS:         2,
	}
}

func TestPublicHTTPServerMetadataAndHostBoundary(t *testing.T) {
	verifier := func(ctx context.Context, token string, req *http.Request) (*auth.TokenInfo, error) {
		return nil, auth.ErrInvalidToken
	}
	server, err := NewPublicHTTPServer(testPublicController(), testPublicOAuthConfig(), verifier)
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodGet, "https://mesh.example/.well-known/oauth-protected-resource", nil)
	req.Host = "mesh.example"
	rec := httptest.NewRecorder()
	server.Handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("metadata status=%d body=%s", rec.Code, rec.Body.String())
	}
	var metadata map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &metadata); err != nil {
		t.Fatal(err)
	}
	if metadata["resource"] != "https://mesh.example/mcp" {
		t.Fatalf("wrong resource metadata: %#v", metadata)
	}
	scopes, _ := metadata["scopes_supported"].([]any)
	joined := make([]string, 0, len(scopes))
	for _, raw := range scopes {
		if s, ok := raw.(string); ok {
			joined = append(joined, s)
		}
	}
	if strings.Join(joined, " ") != "computer.profile computer.read" {
		t.Fatalf("unexpected supported scopes: %#v", metadata["scopes_supported"])
	}

	bad := httptest.NewRequest(http.MethodGet, "https://evil.example/.well-known/oauth-protected-resource", nil)
	bad.Host = "evil.example"
	badRec := httptest.NewRecorder()
	server.Handler.ServeHTTP(badRec, bad)
	if badRec.Code != http.StatusMisdirectedRequest {
		t.Fatalf("host mismatch did not fail closed: %d", badRec.Code)
	}
}

func TestPublicHTTPServerMCPRequiresBearerAndAdvertisesMetadata(t *testing.T) {
	verifier := func(ctx context.Context, token string, req *http.Request) (*auth.TokenInfo, error) {
		return nil, auth.ErrInvalidToken
	}
	server, err := NewPublicHTTPServer(testPublicController(), testPublicOAuthConfig(), verifier)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "https://mesh.example/mcp", strings.NewReader("{}"))
	req.Host = "mesh.example"
	rec := httptest.NewRecorder()
	server.Handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("missing bearer status=%d body=%s", rec.Code, rec.Body.String())
	}
	challenge := rec.Header().Get("WWW-Authenticate")
	if !strings.Contains(challenge, "resource_metadata=\"https://mesh.example/.well-known/oauth-protected-resource\"") {
		t.Fatalf("missing protected-resource metadata challenge: %q", challenge)
	}
}

func TestPublicHTTPServerRejectsRootResource(t *testing.T) {
	cfg := testPublicOAuthConfig()
	cfg.ExpectedResource = "https://mesh.example/"
	_, err := NewPublicHTTPServer(testPublicController(), cfg, func(context.Context, string, *http.Request) (*auth.TokenInfo, error) {
		return nil, auth.ErrInvalidToken
	})
	if err == nil {
		t.Fatal("root resource path accepted")
	}
}
