package gateway

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// CI supplies a binary built from an exact WorkBridge commit. The default
// gateway suite remains self-contained when that integration input is absent.
func TestWorkBridgeRealServerReadOnlyIntegration(t *testing.T) {
	binary := os.Getenv("WORKBRIDGE_BINARY")
	if binary == "" {
		t.Skip("WORKBRIDGE_BINARY is not set")
	}
	root := t.TempDir()
	path := filepath.Join(root, "fixture.txt")
	if err := os.WriteFile(path, []byte("real WorkBridge to VeraMesh read"), 0o600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(root, "workbridge-config.json")
	config, err := json.Marshal(map[string]any{
		"schema":      "WORKBRIDGE_CONFIG_V1",
		"read_roots":  []string{root},
		"write_roots": []string{},
		"process":     map[string]any{"enabled": false},
		"http":        map[string]any{"listen": address, "path": "/mcp", "bearer_token_env": "WORKBRIDGE_HTTP_TOKEN"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(configPath, config, 0o600); err != nil {
		t.Fatal(err)
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		t.Fatal(err)
	}
	token := hex.EncodeToString(secret)
	cmd := exec.Command(binary, "--config", configPath, "--transport", "http")
	cmd.Env = append(os.Environ(), "WORKBRIDGE_HTTP_TOKEN="+token)
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()

	healthURL := "http://" + address + "/mcp/healthz"
	ready := false
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		req, err := http.NewRequest(http.MethodGet, healthURL, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		probe := &http.Client{Timeout: 500 * time.Millisecond}
		resp, err := probe.Do(req)
		if err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				ready = true
				break
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !ready {
		t.Fatal("real WorkBridge did not become ready")
	}
	t.Setenv("WORKBRIDGE_HTTP_TOKEN", token)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := NewWorkBridgeClient(ctx, WorkBridgeUpstreamConfig{
		Schema:         WorkBridgeUpstreamSchema,
		Endpoint:       "http://" + address + "/mcp",
		BearerTokenEnv: "WORKBRIDGE_HTTP_TOKEN",
		TimeoutSeconds: 5,
		AllowedRoots:   []string{root},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if !client.SupportsPublic("read_file") || client.SupportsPublic("write_file") {
		t.Fatal("real WorkBridge advertised the wrong read-only capabilities")
	}
	read, err := client.CallPublic(ctx, "read_file", map[string]any{"path": path, "encoding": "utf-8"})
	if err != nil {
		t.Fatal(err)
	}
	if read["content"] != "real WorkBridge to VeraMesh read" || read["backend"] != "workbridge" {
		t.Fatalf("unexpected real WorkBridge read: %#v", read)
	}
	stat, err := client.CallPublic(ctx, "stat_path", map[string]any{"path": path})
	if err != nil || stat["backend"] != "workbridge" {
		t.Fatalf("real WorkBridge stat failed: %#v, %v", stat, err)
	}
	listed, err := client.CallPublic(ctx, "list_directory", map[string]any{"path": root})
	if err != nil {
		t.Fatal(err)
	}
	entries, ok := listed["entries"].([]any)
	if !ok || len(entries) == 0 {
		t.Fatalf("real WorkBridge list returned no entries: %#v", listed)
	}
}
