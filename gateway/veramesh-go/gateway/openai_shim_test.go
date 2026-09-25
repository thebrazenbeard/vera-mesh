package gateway

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

func toolWire(name string, policy ToolPolicy) map[string]any {
	return map[string]any{
		"name": name,
		"description": policy.Description,
		"inputSchema": map[string]any{"type":"object"},
		"_meta": map[string]any{
			"securitySchemes": securitySchemes(policy),
		},
	}
}

func TestInjectOpenAISecuritySchemesAddsOnlyTopLevelExtension(t *testing.T) {
	policy := PublicToolByName["read_file"]
	envelope := map[string]any{
		"jsonrpc":"2.0",
		"id":1,
		"result":map[string]any{
			"tools":[]any{toolWire("read_file", policy)},
			"nextCursor":"next",
		},
	}
	before, _ := json.Marshal(envelope)
	updated, changed, err := injectOpenAISecuritySchemes(before)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("tools/list was not recognized")
	}
	var got map[string]any
	if err := json.Unmarshal(updated, &got); err != nil {
		t.Fatal(err)
	}
	result := got["result"].(map[string]any)
	tools := result["tools"].([]any)
	tool := tools[0].(map[string]any)
	if !reflect.DeepEqual(
		normalizeJSONValue(tool["securitySchemes"]),
		normalizeJSONValue(securitySchemes(policy)),
	) {
		t.Fatalf("top-level securitySchemes mismatch: %#v", tool)
	}
	meta := tool["_meta"].(map[string]any)
	if !reflect.DeepEqual(
		normalizeJSONValue(meta["securitySchemes"]),
		normalizeJSONValue(securitySchemes(policy)),
	) {
		t.Fatalf("compatibility metadata changed: %#v", meta)
	}
	if result["nextCursor"] != "next" {
		t.Fatalf("non-security response field changed: %#v", result)
	}
}

func TestInjectOpenAISecuritySchemesPassesNonToolsResponseUnchanged(t *testing.T) {
	body := []byte(`{"jsonrpc":"2.0","id":1,"result":{"content":[]}}`)
	updated, changed, err := injectOpenAISecuritySchemes(body)
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Fatal("non-tools response reported changed")
	}
	if string(updated) != string(body) {
		t.Fatalf("non-tools response bytes changed: %s", updated)
	}
}

func TestInjectOpenAISecuritySchemesFailsClosedOnUnknownOrDriftedTool(t *testing.T) {
	cases := []map[string]any{
		{"name":"unknown","inputSchema":map[string]any{"type":"object"},"_meta":map[string]any{"securitySchemes":[]any{}}},
		func() map[string]any {
			v := toolWire("read_file", PublicToolByName["read_file"])
			v["_meta"] = map[string]any{
				"securitySchemes":[]any{map[string]any{"type":"oauth2","scopes":[]any{"computer.write"}}},
			}
			return v
		}(),
		func() map[string]any {
			v := toolWire("read_file", PublicToolByName["read_file"])
			delete(v, "_meta")
			return v
		}(),
	}
	for _, tool := range cases {
		raw, _ := json.Marshal(map[string]any{
			"jsonrpc":"2.0","id":1,
			"result":map[string]any{"tools":[]any{tool}},
		})
		if _, _, err := injectOpenAISecuritySchemes(raw); err == nil {
			t.Fatalf("invalid tool descriptor accepted: %s", raw)
		}
	}
}

func TestInjectOpenAISecuritySchemesRejectsTrailingGarbage(t *testing.T) {
	policy := PublicToolByName["read_file"]
	raw, _ := json.Marshal(map[string]any{
		"jsonrpc":"2.0","id":1,
		"result":map[string]any{"tools":[]any{toolWire("read_file",policy)}},
	})
	raw = append(raw, []byte(" garbage")...)
	if _, _, err := injectOpenAISecuritySchemes(raw); err == nil {
		t.Fatal("trailing invalid JSON accepted")
	}
}

func TestOpenAISecuritySchemeShimFailsHTTPClosedOnMetadataDrift(t *testing.T) {
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type","application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"jsonrpc":"2.0","id":1,
			"result":map[string]any{
				"tools":[]any{
					map[string]any{
						"name":"read_file",
						"inputSchema":map[string]any{"type":"object"},
						"_meta":map[string]any{
							"securitySchemes":[]any{
								map[string]any{"type":"oauth2","scopes":[]any{"wrong"}},
							},
						},
					},
				},
			},
		})
	})
	req := httptest.NewRequest(http.MethodPost, "https://mesh.example/mcp", strings.NewReader("{}"))
	rec := httptest.NewRecorder()
	OpenAISecuritySchemeShim(next).ServeHTTP(rec, req)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("drift did not fail closed: %d %s", rec.Code, rec.Body.String())
	}
}

func TestOpenAISecuritySchemeShimLeavesOAuthErrorResponsesUntouched(t *testing.T) {
	const body = `{"error":"invalid_token"}`
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type","application/json")
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(body))
	})
	req := httptest.NewRequest(http.MethodPost, "https://mesh.example/mcp", nil)
	rec := httptest.NewRecorder()
	OpenAISecuritySchemeShim(next).ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized || strings.TrimSpace(rec.Body.String()) != body {
		t.Fatalf("OAuth error changed: %d %s", rec.Code, rec.Body.String())
	}
}
