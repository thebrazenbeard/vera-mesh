package gateway

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"
)

type policySnapshot struct {
	Schema      string            `json:"schema"`
	OAuthScopes map[string]string `json:"oauth_scopes"`
	Tools       []struct {
		Name         string   `json:"name"`
		Operation    string   `json:"operation"`
		Scopes       []string `json:"scopes"`
		Capabilities []string `json:"capabilities"`
		Annotations  struct {
			ReadOnly    bool `json:"readOnlyHint"`
			Destructive bool `json:"destructiveHint"`
			Idempotent  bool `json:"idempotentHint"`
			OpenWorld   bool `json:"openWorldHint"`
		} `json:"annotations"`
	} `json:"tools"`
}

func repoRoot(t *testing.T) string {
	t.Helper()
	_, here, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate current test file")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(here), "..", "..", ".."))
}

func TestGoPublicPolicyMatchesCanonicalJSONSnapshot(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(
		repoRoot(t), "protocol", "veraport", "v1", "public-tool-policy.json",
	))
	if err != nil {
		t.Fatal(err)
	}
	var snapshot policySnapshot
	if err := json.Unmarshal(raw, &snapshot); err != nil {
		t.Fatal(err)
	}
	if snapshot.Schema != "VERAMESH_PUBLIC_TOOL_POLICY_V1" {
		t.Fatalf("unexpected policy schema: %q", snapshot.Schema)
	}
	if !reflect.DeepEqual(snapshot.OAuthScopes, Scopes) {
		t.Fatalf("OAuth scope catalog drift\nGo: %#v\nJSON: %#v", Scopes, snapshot.OAuthScopes)
	}
	if len(snapshot.Tools) != len(PublicTools) {
		t.Fatalf("tool count drift: Go=%d JSON=%d", len(PublicTools), len(snapshot.Tools))
	}
	byName := make(map[string]ToolPolicy, len(PublicTools))
	for _, tool := range PublicTools {
		if _, exists := byName[tool.Name]; exists {
			t.Fatalf("duplicate Go public tool: %s", tool.Name)
		}
		byName[tool.Name] = tool
	}
	for _, want := range snapshot.Tools {
		got, ok := byName[want.Name]
		if !ok {
			t.Fatalf("Go policy missing %s", want.Name)
		}
		if got.Operation != want.Operation ||
			!reflect.DeepEqual(got.Scopes, want.Scopes) ||
			!reflect.DeepEqual(got.Capabilities, want.Capabilities) ||
			got.ReadOnly != want.Annotations.ReadOnly ||
			got.Destructive != want.Annotations.Destructive ||
			got.Idempotent != want.Annotations.Idempotent ||
			got.OpenWorld != want.Annotations.OpenWorld {
			t.Fatalf("policy drift for %s\nGo: %#v\nJSON: %#v", want.Name, got, want)
		}
		schemes := got.SecuritySchemes()
		if len(schemes) != 1 || schemes[0].Type != "oauth2" ||
			!reflect.DeepEqual(schemes[0].Scopes, want.Scopes) {
			t.Fatalf("security scheme drift for %s: %#v", want.Name, schemes)
		}
	}
}
