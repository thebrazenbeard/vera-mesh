package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const WorkBridgeUpstreamSchema = "VERAMESH_WORKBRIDGE_UPSTREAM_V1"

var workBridgeEnvName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

type WorkBridgeUpstreamConfig struct {
	Schema         string  `json:"schema"`
	Endpoint       string  `json:"endpoint"`
	BearerTokenEnv string  `json:"bearer_token_env"`
	TimeoutSeconds float64 `json:"timeout_seconds"`
}

func LoadWorkBridgeUpstreamConfig(path string) (WorkBridgeUpstreamConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return WorkBridgeUpstreamConfig{}, fmt.Errorf("read WorkBridge config: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	var cfg WorkBridgeUpstreamConfig
	if err := dec.Decode(&cfg); err != nil {
		return WorkBridgeUpstreamConfig{}, fmt.Errorf("decode WorkBridge config: %w", err)
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return WorkBridgeUpstreamConfig{}, errors.New("WorkBridge config contains trailing JSON")
	}
	if cfg.TimeoutSeconds == 0 {
		cfg.TimeoutSeconds = 10
	}
	if err := cfg.Validate(); err != nil {
		return WorkBridgeUpstreamConfig{}, err
	}
	return cfg, nil
}

func (c WorkBridgeUpstreamConfig) Validate() error {
	if c.Schema != WorkBridgeUpstreamSchema {
		return fmt.Errorf("WorkBridge schema must be %q", WorkBridgeUpstreamSchema)
	}
	u, err := url.Parse(strings.TrimSpace(c.Endpoint))
	if err != nil || u.Scheme == "" || u.Host == "" {
		return errors.New("WorkBridge endpoint must be an absolute URL")
	}
	if u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("WorkBridge endpoint must not contain userinfo, query, or fragment")
	}
	if u.Scheme != "https" && u.Scheme != "http" {
		return errors.New("WorkBridge endpoint scheme must be https or loopback http")
	}
	if u.EscapedPath() == "" || u.EscapedPath() == "/" || strings.HasSuffix(u.EscapedPath(), "/") {
		return errors.New("WorkBridge endpoint must use a dedicated non-root MCP path without trailing slash")
	}
	if u.Scheme == "http" {
		host := u.Hostname()
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			return errors.New("plain HTTP WorkBridge endpoint is allowed only on a literal loopback IP")
		}
	}
	if !workBridgeEnvName.MatchString(c.BearerTokenEnv) {
		return errors.New("WorkBridge bearer_token_env must be a valid environment-variable name")
	}
	if c.TimeoutSeconds <= 0 || c.TimeoutSeconds > 60 {
		return errors.New("WorkBridge timeout_seconds must be > 0 and <= 60")
	}
	return nil
}

type workBridgeBearerTransport struct {
	token string
	base  http.RoundTripper
}

func (t *workBridgeBearerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	clone := req.Clone(req.Context())
	clone.Header = req.Header.Clone()
	clone.Header.Set("Authorization", "Bearer "+t.token)
	return base.RoundTrip(clone)
}

type WorkBridgeClient struct {
	cfg       WorkBridgeUpstreamConfig
	session   *mcp.ClientSession
	available map[string]struct{}
}

func NewWorkBridgeClient(ctx context.Context, cfg WorkBridgeUpstreamConfig) (*WorkBridgeClient, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	token, err := loadWorkBridgeBearerToken(cfg.BearerTokenEnv)
	if err != nil {
		return nil, err
	}
	httpClient := &http.Client{
		Timeout: time.Duration(cfg.TimeoutSeconds * float64(time.Second)),
		Transport: &workBridgeBearerTransport{token: token},
	}
	client := mcp.NewClient(&mcp.Implementation{Name: "VeraMesh WorkBridge Upstream", Version: "0.1.0"}, nil)
	session, err := client.Connect(ctx, &mcp.StreamableClientTransport{
		Endpoint:   cfg.Endpoint,
		HTTPClient: httpClient,
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("connect WorkBridge: %w", err)
	}
	w := &WorkBridgeClient{
		cfg:       cfg,
		session:   session,
		available: map[string]struct{}{},
	}
	tools, err := session.ListTools(ctx, nil)
	if err != nil {
		_ = session.Close()
		return nil, fmt.Errorf("list WorkBridge tools: %w", err)
	}
	for _, tool := range tools.Tools {
		w.available[tool.Name] = struct{}{}
	}
	if _, ok := w.available["workbridge_health"]; !ok {
		_ = session.Close()
		return nil, errors.New("WorkBridge is missing required workbridge_health tool")
	}
	if _, err := w.call(ctx, "workbridge_health", map[string]any{}); err != nil {
		_ = session.Close()
		return nil, fmt.Errorf("WorkBridge health probe: %w", err)
	}
	return w, nil
}

func loadWorkBridgeBearerToken(name string) (string, error) {
	token, ok := os.LookupEnv(name)
	if !ok || token == "" {
		return "", fmt.Errorf("WorkBridge bearer token environment variable %s is not set", name)
	}
	if len(token) < 32 {
		return "", errors.New("WorkBridge bearer token must be at least 32 bytes")
	}
	if strings.IndexFunc(token, unicode.IsSpace) >= 0 {
		return "", errors.New("WorkBridge bearer token must not contain whitespace")
	}
	return token, nil
}

func (w *WorkBridgeClient) Close() error {
	if w == nil || w.session == nil {
		return nil
	}
	return w.session.Close()
}

var workBridgePublicToolMap = map[string]string{
	"read_file":      "workspace_read_text",
	"stat_path":      "workspace_stat",
	"list_directory": "workspace_list",
	"write_file":     "workspace_write_text",
	"make_directory": "workspace_mkdir",
}

func (w *WorkBridgeClient) SupportsPublic(publicName string) bool {
	if w == nil {
		return false
	}
	upstream, ok := workBridgePublicToolMap[publicName]
	if !ok {
		return false
	}
	_, ok = w.available[upstream]
	return ok
}

func (w *WorkBridgeClient) CanHandlePublic(publicName string, args map[string]any) bool {
	if !w.SupportsPublic(publicName) {
		return false
	}
	switch publicName {
	case "read_file", "write_file":
		encoding := optString(args, "encoding", "utf-8")
		return strings.EqualFold(encoding, "utf-8") || strings.EqualFold(encoding, "utf8")
	case "make_directory":
		parents, err := optBool(args, "parents", true)
		return err == nil && !parents
	default:
		return true
	}
}

func (w *WorkBridgeClient) CallPublic(
	ctx context.Context,
	publicName string,
	args map[string]any,
) (map[string]any, error) {
	if !w.CanHandlePublic(publicName, args) {
		return nil, fmt.Errorf("WorkBridge cannot preserve semantics for public tool %s", publicName)
	}
	switch publicName {
	case "read_file":
		path, err := reqString(args, "path")
		if err != nil {
			return nil, err
		}
		out, err := w.call(ctx, "workspace_read_text", map[string]any{"path": path})
		if err != nil {
			return nil, err
		}
		text, _ := out["text"].(string)
		return map[string]any{
			"path": path, "content": text, "encoding": "utf-8", "backend": "workbridge",
		}, nil
	case "stat_path":
		path, err := reqString(args, "path")
		if err != nil {
			return nil, err
		}
		out, err := w.call(ctx, "workspace_stat", map[string]any{"path": path})
		if err != nil {
			return nil, err
		}
		stat, ok := out["stat"].(map[string]any)
		if !ok {
			return nil, errors.New("WorkBridge workspace_stat returned invalid structured content")
		}
		stat["backend"] = "workbridge"
		return stat, nil
	case "list_directory":
		path, err := reqString(args, "path")
		if err != nil {
			return nil, err
		}
		offset, err := optInt(args, "offset", 0)
		if err != nil {
			return nil, err
		}
		maxEntries, err := optInt(args, "max_entries", 200)
		if err != nil {
			return nil, err
		}
		if offset < 0 || maxEntries < 1 {
			return nil, errors.New("offset must be >= 0 and max_entries must be >= 1")
		}
		out, err := w.call(ctx, "workspace_list", map[string]any{"path": path})
		if err != nil {
			return nil, err
		}
		rawEntries, ok := out["entries"].([]any)
		if !ok {
			return nil, errors.New("WorkBridge workspace_list returned invalid structured content")
		}
		if offset > len(rawEntries) {
			offset = len(rawEntries)
		}
		end := offset + maxEntries
		if end > len(rawEntries) {
			end = len(rawEntries)
		}
		next := end
		eof := end >= len(rawEntries)
		return map[string]any{
			"path": path,
			"entries": rawEntries[offset:end],
			"offset": offset,
			"next_offset": next,
			"eof": eof,
			"backend": "workbridge",
		}, nil
	case "write_file":
		path, err := reqString(args, "path")
		if err != nil {
			return nil, err
		}
		content, err := reqStringAllowEmpty(args, "content")
		if err != nil {
			return nil, err
		}
		out, err := w.call(ctx, "workspace_write_text", map[string]any{
			"path": path, "content": content, "overwrite": true,
		})
		if err != nil {
			return nil, err
		}
		out["path"] = path
		out["backend"] = "workbridge"
		return out, nil
	case "make_directory":
		path, err := reqString(args, "path")
		if err != nil {
			return nil, err
		}
		out, err := w.call(ctx, "workspace_mkdir", map[string]any{"path": path})
		if err != nil {
			return nil, err
		}
		out["path"] = path
		out["backend"] = "workbridge"
		return out, nil
	default:
		return nil, fmt.Errorf("unsupported WorkBridge public mapping: %s", publicName)
	}
}

func (w *WorkBridgeClient) call(ctx context.Context, tool string, arguments map[string]any) (map[string]any, error) {
	if _, ok := w.available[tool]; !ok {
		return nil, fmt.Errorf("WorkBridge tool %s is unavailable", tool)
	}
	result, err := w.session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: arguments})
	if err != nil {
		return nil, err
	}
	if result.IsError {
		return nil, fmt.Errorf("WorkBridge tool %s returned an application error", tool)
	}
	raw, err := json.Marshal(result.StructuredContent)
	if err != nil {
		return nil, fmt.Errorf("encode WorkBridge structured content: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var out map[string]any
	if err := dec.Decode(&out); err != nil {
		return nil, fmt.Errorf("decode WorkBridge structured content: %w", err)
	}
	if out == nil {
		out = map[string]any{}
	}
	return out, nil
}
