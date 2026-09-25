package gateway

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"reflect"
)

const maxShimResponseBytes = 4 * 1024 * 1024

type bufferedResponseWriter struct {
	header http.Header
	status int
	body   bytes.Buffer
	err    error
}

func newBufferedResponseWriter() *bufferedResponseWriter {
	return &bufferedResponseWriter{header: make(http.Header)}
}

func (w *bufferedResponseWriter) Header() http.Header { return w.header }

func (w *bufferedResponseWriter) WriteHeader(status int) {
	if w.status == 0 {
		w.status = status
	}
}

func (w *bufferedResponseWriter) Write(p []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	if w.err != nil {
		return 0, w.err
	}
	if w.body.Len()+len(p) > maxShimResponseBytes {
		w.err = errors.New("MCP JSON response exceeds OpenAI shim bound")
		return 0, w.err
	}
	return w.body.Write(p)
}

func OpenAISecuritySchemeShim(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := newBufferedResponseWriter()
		next.ServeHTTP(rec, r)
		if rec.status == 0 {
			rec.status = http.StatusOK
		}
		if rec.err != nil {
			http.Error(w, "MCP response exceeded security shim policy", http.StatusInternalServerError)
			return
		}

		body := rec.body.Bytes()
		contentType := rec.header.Get("Content-Type")
		if rec.status >= 200 && rec.status < 300 &&
			len(body) != 0 &&
			(contentType == "" || hasJSONContentType(contentType)) {
			updated, changed, err := injectOpenAISecuritySchemes(body)
			if err != nil {
				http.Error(w, "invalid MCP tools/list security metadata", http.StatusInternalServerError)
				return
			}
			if changed {
				body = updated
				rec.header.Del("Content-Length")
			}
		}

		for key, values := range rec.header {
			for _, value := range values {
				w.Header().Add(key, value)
			}
		}
		w.WriteHeader(rec.status)
		_, _ = w.Write(body)
	})
}

func hasJSONContentType(value string) bool {
	for i := 0; i+16 <= len(value); i++ {
		if value[i:i+16] == "application/json" {
			return true
		}
	}
	return false
}

func injectOpenAISecuritySchemes(body []byte) ([]byte, bool, error) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var envelope map[string]any
	if err := dec.Decode(&envelope); err != nil {
		return nil, false, err
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, false, errors.New("multiple JSON values in MCP response")
		}
		return nil, false, fmt.Errorf("trailing invalid JSON: %w", err)
	}
	result, ok := envelope["result"].(map[string]any)
	if !ok {
		return body, false, nil
	}
	rawTools, exists := result["tools"]
	if !exists {
		return body, false, nil
	}
	tools, ok := rawTools.([]any)
	if !ok {
		return nil, false, errors.New("tools/list result tools must be an array")
	}

	for i, raw := range tools {
		tool, ok := raw.(map[string]any)
		if !ok {
			return nil, false, fmt.Errorf("tools[%d] must be an object", i)
		}
		name, ok := tool["name"].(string)
		if !ok || name == "" {
			return nil, false, fmt.Errorf("tools[%d] has no name", i)
		}
		policy, ok := PublicToolByName[name]
		if !ok {
			return nil, false, fmt.Errorf("unknown public tool in tools/list: %s", name)
		}
		expected := normalizeJSONValue(securitySchemes(policy))

		meta, ok := tool["_meta"].(map[string]any)
		if !ok {
			return nil, false, fmt.Errorf("%s missing _meta security compatibility object", name)
		}
		compat, exists := meta["securitySchemes"]
		if !exists {
			return nil, false, fmt.Errorf("%s missing _meta.securitySchemes", name)
		}
		if !reflect.DeepEqual(normalizeJSONValue(compat), expected) {
			return nil, false, fmt.Errorf("%s _meta.securitySchemes drifted from policy", name)
		}
		if existing, exists := tool["securitySchemes"]; exists &&
			!reflect.DeepEqual(normalizeJSONValue(existing), expected) {
			return nil, false, fmt.Errorf("%s top-level securitySchemes conflicts with policy", name)
		}
		tool["securitySchemes"] = securitySchemes(policy)
	}
	updated, err := json.Marshal(envelope)
	if err != nil {
		return nil, false, err
	}
	return updated, true, nil
}

func normalizeJSONValue(value any) any {
	raw, err := json.Marshal(value)
	if err != nil {
		return value
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var normalized any
	if err := dec.Decode(&normalized); err != nil {
		return value
	}
	return normalized
}
