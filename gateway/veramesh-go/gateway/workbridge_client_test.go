package gateway

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestWorkBridgeClientDiscoversAndMapsTools(t *testing.T) {
	const token = "0123456789abcdef0123456789abcdef"
	t.Setenv("WORKBRIDGE_TEST_TOKEN", token)

	server := mcp.NewServer(&mcp.Implementation{Name: "workbridge-test", Version: "0"}, nil)
	mcp.AddTool(server, &mcp.Tool{Name: "workbridge_health"}, func(context.Context, *mcp.CallToolRequest, struct{}) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"version": "test", "read_enabled": true, "write_enabled": true}, nil
	})
	mcp.AddTool(server, &mcp.Tool{Name: "workspace_read_text"}, func(_ context.Context, _ *mcp.CallToolRequest, in struct{ Path string `json:"path"` }) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"text": "hello:" + in.Path}, nil
	})
	mcp.AddTool(server, &mcp.Tool{Name: "workspace_stat"}, func(_ context.Context, _ *mcp.CallToolRequest, in struct{ Path string `json:"path"` }) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"stat": map[string]any{"path": in.Path, "size": 5}}, nil
	})
	mcp.AddTool(server, &mcp.Tool{Name: "workspace_list"}, func(_ context.Context, _ *mcp.CallToolRequest, in struct{ Path string `json:"path"` }) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"entries": []map[string]any{{"name": "a"}, {"name": "b"}, {"name": "c"}}}, nil
	})
	mcp.AddTool(server, &mcp.Tool{Name: "workspace_write_text"}, func(_ context.Context, _ *mcp.CallToolRequest, in struct {
		Path string `json:"path"`
		Content string `json:"content"`
		Overwrite bool `json:"overwrite"`
	}) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"bytes_written": len(in.Content), "overwrite": in.Overwrite}, nil
	})
	mcp.AddTool(server, &mcp.Tool{Name: "workspace_mkdir"}, func(_ context.Context, _ *mcp.CallToolRequest, in struct{ Path string `json:"path"` }) (*mcp.CallToolResult, map[string]any, error) {
		return nil, map[string]any{"created": true}, nil
	})

	stream := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return server }, &mcp.StreamableHTTPOptions{Stateless: true})
	httpServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		stream.ServeHTTP(w, r)
	}))
	defer httpServer.Close()

	cfg := WorkBridgeUpstreamConfig{
		Schema: WorkBridgeUpstreamSchema,
		Endpoint: httpServer.URL + "/mcp",
		BearerTokenEnv: "WORKBRIDGE_TEST_TOKEN",
		TimeoutSeconds: 5,
		AllowedRoots: []string{"/tmp"},
	}
	client, err := NewWorkBridgeClient(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	if !client.SupportsPublic("read_file") || !client.SupportsPublic("write_file") {
		t.Fatal("expected mapped WorkBridge tools to be discovered")
	}
	read, err := client.CallPublic(context.Background(), "read_file", map[string]any{"path": "/tmp/x", "encoding": "utf-8"})
	if err != nil {
		t.Fatal(err)
	}
	if read["content"] != "hello:/tmp/x" || read["backend"] != "workbridge" {
		t.Fatalf("unexpected read result: %#v", read)
	}
	listed, err := client.CallPublic(context.Background(), "list_directory", map[string]any{"path": "/tmp", "offset": 1, "max_entries": 1})
	if err != nil {
		t.Fatal(err)
	}
	entries, ok := listed["entries"].([]any)
	if !ok || len(entries) != 1 {
		t.Fatalf("unexpected list result: %#v", listed)
	}
}

func TestWorkBridgeConfigRejectsInsecureRemoteHTTP(t *testing.T) {
	cfg := WorkBridgeUpstreamConfig{
		Schema: WorkBridgeUpstreamSchema,
		Endpoint: "http://100.64.0.1:8765/mcp",
		BearerTokenEnv: "WORKBRIDGE_HTTP_TOKEN",
		TimeoutSeconds: 5,
		AllowedRoots: []string{"/tmp"},
	}
	if err := cfg.Validate(); err == nil {
		t.Fatal("remote plaintext WorkBridge endpoint accepted")
	}
	cfg.Endpoint = "https://lappy.example.test/mcp"
	if err := cfg.Validate(); err != nil {
		t.Fatalf("HTTPS WorkBridge endpoint rejected: %v", err)
	}
}

func TestLoadWorkBridgeBearerToken(t *testing.T) {
	const name = "WORKBRIDGE_TOKEN_TEST"
	_ = os.Unsetenv(name)
	if _, err := loadWorkBridgeBearerToken(name); err == nil {
		t.Fatal("missing token accepted")
	}
	t.Setenv(name, "short")
	if _, err := loadWorkBridgeBearerToken(name); err == nil {
		t.Fatal("short token accepted")
	}
}

func TestWorkBridgeBackendRootCeiling(t *testing.T) {
	client := &WorkBridgeClient{
		available: map[string]struct{}{"workspace_read_text": {}},
		allowedRoots: []string{"C:/Users/Patrick"},
	}
	if !client.CanHandlePublic("read_file", map[string]any{
		"path": "c:/users/patrick/repo/README.md",
		"encoding": "utf-8",
	}) {
		t.Fatal("case-insensitive in-root Windows path was rejected")
	}
	if client.CanHandlePublic("read_file", map[string]any{
		"path": "C:/Windows/System32/drivers/etc/hosts",
		"encoding": "utf-8",
	}) {
		t.Fatal("out-of-root WorkBridge path was admitted")
	}
}

func TestWorkBridgeConfigRequiresExplicitIntegrationRoots(t *testing.T) {
	cfg := WorkBridgeUpstreamConfig{
		Schema: WorkBridgeUpstreamSchema,
		Endpoint: "https://lappy.example.test/mcp",
		BearerTokenEnv: "WORKBRIDGE_HTTP_TOKEN",
		TimeoutSeconds: 5,
	}
	if err := cfg.Validate(); err == nil {
		t.Fatal("WorkBridge config without allowed_roots was accepted")
	}
}
