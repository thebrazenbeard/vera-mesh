package gateway

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/auth"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/modelcontextprotocol/go-sdk/oauthex"
)

type PublicHTTPServer struct {
	Gateway             *MCPGateway
	Handler             http.Handler
	Resource            string
	ResourceMetadataURL string
}

func NewPublicHTTPServer(
	controller *Controller,
	oauthConfig IntrospectionConfig,
	verifier auth.TokenVerifier,
) (*PublicHTTPServer, error) {
	if controller == nil {
		return nil, errors.New("controller is required")
	}
	if verifier == nil {
		return nil, errors.New("OAuth token verifier is required")
	}
	if err := oauthConfig.Validate(); err != nil {
		return nil, err
	}
	resourceURL, err := url.Parse(oauthConfig.ExpectedResource)
	if err != nil || resourceURL.Scheme != "https" || resourceURL.Host == "" {
		return nil, errors.New("expected_resource must be an absolute HTTPS URL")
	}
	if resourceURL.RawQuery != "" || resourceURL.Fragment != "" || resourceURL.User != nil {
		return nil, errors.New("expected_resource must not contain userinfo, query, or fragment")
	}
	resourcePath := resourceURL.EscapedPath()
	if resourcePath == "" {
		resourcePath = "/"
	}
	if resourcePath == "/" {
		return nil, errors.New("expected_resource must use a dedicated MCP path")
	}
	if strings.HasSuffix(resourcePath, "/") {
		return nil, errors.New("expected_resource MCP path must not end with slash")
	}

	gateway, err := NewMCPGateway(controller, oauthConfig.ExpectedIssuer)
	if err != nil {
		return nil, err
	}
	metadataURL := resourceURL.Scheme + "://" + resourceURL.Host + "/.well-known/oauth-protected-resource"
	metadata := &oauthex.ProtectedResourceMetadata{
		Resource:             oauthConfig.ExpectedResource,
		AuthorizationServers: []string{oauthConfig.ExpectedIssuer},
		ScopesSupported:      supportedPublicScopes(controller.Config()),
		BearerMethodsSupported: []string{"header"},
		ResourceName:         "VeraMesh Public Workstation Gateway",
	}

	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server {
		return gateway.Server()
	}, nil)
	protected := auth.RequireBearerToken(verifier, &auth.RequireBearerTokenOptions{
		ResourceMetadataURL: metadataURL,
	})(OpenAISecuritySchemeShim(stream))

	mux := http.NewServeMux()
	mux.Handle(resourcePath, exactPath(resourcePath, protected))
	mux.Handle("/.well-known/oauth-protected-resource",
		exactPath("/.well-known/oauth-protected-resource", auth.ProtectedResourceMetadataHandler(metadata)))

	handler := requirePublicHost(resourceURL.Host, mux)
	return &PublicHTTPServer{
		Gateway:             gateway,
		Handler:             handler,
		Resource:            oauthConfig.ExpectedResource,
		ResourceMetadataURL: metadataURL,
	}, nil
}

func supportedPublicScopes(cfg *Config) []string {
	set := map[string]struct{}{}
	for _, policy := range PublicTools {
		if !ToolSupported(cfg, policy) {
			continue
		}
		for _, scope := range policy.Scopes {
			set[scope] = struct{}{}
		}
	}
	out := make([]string, 0, len(set))
	for scope := range set {
		out = append(out, scope)
	}
	sort.Strings(out)
	return out
}

func exactPath(expected string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != expected {
			http.NotFound(w, r)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func requirePublicHost(expected string, next http.Handler) http.Handler {
	expected = strings.ToLower(strings.TrimSpace(expected))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.ToLower(strings.TrimSpace(r.Host)) != expected {
			http.Error(w, fmt.Sprintf("unexpected public host %q", r.Host), http.StatusMisdirectedRequest)
			return
		}
		next.ServeHTTP(w, r)
	})
}
