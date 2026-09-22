package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sort"
	"strings"
	"sync"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type MCPGateway struct {
	controller          *Controller
	issuer              string
	resourceMetadataURL string
	server              *mcp.Server

	mu      sync.Mutex
	facades map[string]*Facade
}

func NewMCPGateway(controller *Controller, issuer, resourceMetadataURL string) (*MCPGateway, error) {
	if controller == nil {
		return nil, errors.New("controller is required")
	}
	issuer = strings.TrimRight(strings.TrimSpace(issuer), "/")
	if issuer == "" {
		return nil, errors.New("issuer is required")
	}
	resourceMetadataURL = strings.TrimSpace(resourceMetadataURL)
	if resourceMetadataURL == "" {
		return nil, errors.New("resource metadata URL is required")
	}
	g := &MCPGateway{
		controller:          controller,
		issuer:              issuer,
		resourceMetadataURL: resourceMetadataURL,
		server: mcp.NewServer(
			&mcp.Implementation{
				Name:    "VeraMesh Public Workstation Gateway",
				Version: "0.1.0",
			},
			nil,
		),
		facades: map[string]*Facade{},
	}
	for _, policy := range PublicTools {
		if !ToolSupported(controller.Config(), policy) {
			continue
		}
		p := policy
		g.server.AddTool(toolDescriptor(p), func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			return g.callTool(ctx, req, p)
		})
	}
	return g, nil
}

func (g *MCPGateway) Server() *mcp.Server { return g.server }

func (g *MCPGateway) Close(ctx context.Context) map[string]any {
	g.mu.Lock()
	facades := make(map[string]*Facade, len(g.facades))
	for actor, facade := range g.facades {
		facades[actor] = facade
	}
	g.mu.Unlock()

	actors := make([]string, 0, len(facades))
	for actor := range facades {
		actors = append(actors, actor)
	}
	sort.Strings(actors)
	results := map[string]any{}
	for _, actor := range actors {
		results[actor] = facades[actor].Close(ctx)
	}
	return results
}

func (g *MCPGateway) facade(actor string) (*Facade, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	if existing := g.facades[actor]; existing != nil {
		return existing, nil
	}
	facade, err := NewFacade(g.controller, actor)
	if err != nil {
		return nil, err
	}
	g.facades[actor] = facade
	return facade, nil
}

func toolDescriptor(policy ToolPolicy) *mcp.Tool {
	destructive := policy.Destructive
	openWorld := policy.OpenWorld
	return &mcp.Tool{
		Name:        policy.Name,
		Title:       strings.Title(strings.ReplaceAll(policy.Name, "_", " ")),
		Description: policy.Description,
		InputSchema: inputSchema(policy.Name),
		Annotations: &mcp.ToolAnnotations{
			ReadOnlyHint:    policy.ReadOnly,
			DestructiveHint: &destructive,
			IdempotentHint:  policy.Idempotent,
			OpenWorldHint:   &openWorld,
		},
		Meta: mcp.Meta{
			"securitySchemes": securitySchemes(policy),
		},
	}
}

func securitySchemes(policy ToolPolicy) []map[string]any {
	return []map[string]any{{
		"type":   "oauth2",
		"scopes": append([]string(nil), policy.Scopes...),
	}}
}

func (g *MCPGateway) callTool(
	ctx context.Context,
	req *mcp.CallToolRequest,
	policy ToolPolicy,
) (*mcp.CallToolResult, error) {
	if req == nil || req.Params == nil || req.Extra == nil ||
		req.Extra.TokenInfo == nil {
		return g.authErrorToolResult(
			"invalid_token",
			"VeraMesh OAuth token is required",
			policy.Scopes,
		), nil
	}
	token := req.Extra.TokenInfo
	if strings.TrimSpace(token.UserID) == "" {
		return g.authErrorToolResult(
			"invalid_token",
			"OAuth resource-owner subject is required",
			policy.Scopes,
		), nil
	}
	missing := missingScopes(token.Scopes, policy.Scopes)
	if len(missing) != 0 {
		return g.authErrorToolResult(
			"insufficient_scope",
			"Additional VeraMesh permission required: "+strings.Join(missing, ", "),
			policy.Scopes,
		), nil
	}
	facade, err := g.facade(g.issuer + "|" + token.UserID)
	if err != nil {
		return errorToolResult("PUBLIC_GATEWAY_ERROR", err.Error()), nil
	}
	args, err := decodeArguments(req.Params.Arguments)
	if err != nil {
		return errorToolResult("INVALID_ARGUMENTS", err.Error()), nil
	}
	result, err := dispatchTool(ctx, facade, policy.Name, args)
	if err != nil {
		code := "PUBLIC_GATEWAY_ERROR"
		var remote *RemoteError
		if errors.As(err, &remote) {
			code = remote.Code
		}
		return errorToolResult(code, err.Error()), nil
	}
	raw, err := json.Marshal(result)
	if err != nil {
		return errorToolResult("RESULT_ENCODING_ERROR", err.Error()), nil
	}
	return &mcp.CallToolResult{
		Content: []mcp.Content{&mcp.TextContent{Text: string(raw)}},
		StructuredContent: result,
	}, nil
}

func missingScopes(granted, required []string) []string {
	have := map[string]struct{}{}
	for _, scope := range granted {
		have[scope] = struct{}{}
	}
	var missing []string
	for _, scope := range required {
		if _, ok := have[scope]; !ok {
			missing = append(missing, scope)
		}
	}
	sort.Strings(missing)
	return missing
}

func (g *MCPGateway) authErrorToolResult(code, message string, scopes []string) *mcp.CallToolResult {
	result := errorToolResult(code, message)
	challenge := fmt.Sprintf(
		"Bearer resource_metadata=%q, error=%q, error_description=%q",
		g.resourceMetadataURL,
		code,
		message,
	)
	if len(scopes) != 0 {
		challenge += fmt.Sprintf(", scope=%q", strings.Join(scopes, " "))
	}
	result.Meta = mcp.Meta{
		"mcp/www_authenticate": []string{challenge},
	}
	return result
}

func errorToolResult(code, message string) *mcp.CallToolResult {
	raw, _ := json.Marshal(map[string]any{
		"error": map[string]any{
			"code":    code,
			"message": message,
		},
	})
	return &mcp.CallToolResult{
		Content: []mcp.Content{&mcp.TextContent{Text: string(raw)}},
		StructuredContent: map[string]any{
			"error": map[string]any{"code": code, "message": message},
		},
		IsError: true,
	}
}

func decodeArguments(raw json.RawMessage) (map[string]any, error) {
	if len(raw) == 0 {
		return map[string]any{}, nil
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var value map[string]any
	if err := dec.Decode(&value); err != nil {
		return nil, fmt.Errorf("arguments must be a JSON object: %w", err)
	}
	if value == nil {
		return map[string]any{}, nil
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return nil, errors.New("arguments contain trailing JSON")
	}
	return value, nil
}

func dispatchTool(ctx context.Context, f *Facade, name string, a map[string]any) (map[string]any, error) {
	switch name {
	case "computer_info":
		return f.ComputerInfo(ctx)
	case "read_file":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		return f.ReadFile(ctx, p, optString(a, "encoding", "utf-8"))
	case "read_bytes":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		offset, err := optInt64(a, "offset", 0)
		if err != nil { return nil, err }
		var maxBytes any
		if raw, ok := a["max_bytes"]; ok {
			v, err := exactJSONInt64(raw)
			if err != nil { return nil, argError("max_bytes", err) }
			maxBytes = v
		}
		var expected any
		if raw, ok := a["expected_file_version"]; ok {
			v, ok := raw.(string)
			if !ok { return nil, argError("expected_file_version", errors.New("must be string")) }
			expected = v
		}
		return f.ReadBytes(ctx, p, offset, maxBytes, expected)
	case "stat_path":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		return f.StatPath(ctx, p)
	case "list_directory":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		offset, err := optInt(a, "offset", 0)
		if err != nil { return nil, err }
		maxEntries, err := optInt(a, "max_entries", 200)
		if err != nil { return nil, err }
		return f.ListDirectory(ctx, p, offset, maxEntries)
	case "search_files":
		if _, err := reqString(a, "root"); err != nil { return nil, err }
		if _, err := reqString(a, "query"); err != nil { return nil, err }
		applyDefault(a, "offset", 0)
		applyDefault(a, "max_results", 100)
		applyDefault(a, "max_entries", 10000)
		applyDefault(a, "max_depth", 12)
		applyDefault(a, "case_sensitive", false)
		return f.SearchFiles(ctx, a)
	case "search_content":
		if _, err := reqString(a, "root"); err != nil { return nil, err }
		if _, err := reqString(a, "query"); err != nil { return nil, err }
		applyDefault(a, "file_pattern", "*")
		applyDefault(a, "offset", 0)
		applyDefault(a, "max_results", 100)
		applyDefault(a, "max_entries", 10000)
		applyDefault(a, "max_depth", 12)
		applyDefault(a, "max_total_bytes", 8*1024*1024)
		applyDefault(a, "case_sensitive", false)
		return f.SearchContent(ctx, a)
	case "write_file":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		content, err := reqStringAllowEmpty(a, "content")
		if err != nil { return nil, err }
		return f.WriteFile(ctx, p, content, optString(a, "encoding", "utf-8"))
	case "append_file":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		content, err := reqStringAllowEmpty(a, "content")
		if err != nil { return nil, err }
		return f.AppendFile(ctx, p, content, optString(a, "encoding", "utf-8"))
	case "make_directory":
		p, err := reqString(a, "path")
		if err != nil { return nil, err }
		parents, err := optBool(a, "parents", true)
		if err != nil { return nil, err }
		return f.MakeDirectory(ctx, p, parents)
	case "move_path":
		source, err := reqString(a, "source")
		if err != nil { return nil, err }
		destination, err := reqString(a, "destination")
		if err != nil { return nil, err }
		return f.MovePath(ctx, source, destination)
	case "replace_text":
		if _, err := reqString(a, "path"); err != nil { return nil, err }
		if _, err := reqStringAllowEmpty(a, "old_string"); err != nil { return nil, err }
		if _, err := reqStringAllowEmpty(a, "new_string"); err != nil { return nil, err }
		applyDefault(a, "expected_count", 1)
		applyDefault(a, "encoding", "utf-8")
		return f.ReplaceText(ctx, a)
	case "run_process":
		argv, err := reqStringSlice(a, "argv")
		if err != nil { return nil, err }
		cwd, err := reqString(a, "cwd")
		if err != nil { return nil, err }
		timeout, err := optFloat(a, "timeout_s", 60)
		if err != nil { return nil, err }
		return f.RunProcess(ctx, argv, cwd, timeout)
	case "start_process":
		argv, err := reqStringSlice(a, "argv")
		if err != nil { return nil, err }
		cwd, err := reqString(a, "cwd")
		if err != nil { return nil, err }
		maxRuntime, err := optFloat(a, "max_runtime_s", 900)
		if err != nil { return nil, err }
		return f.StartProcess(ctx, argv, cwd, maxRuntime)
	case "list_processes":
		return f.ListProcesses(ctx)
	case "process_status":
		handle, err := reqString(a, "process_handle")
		if err != nil { return nil, err }
		return f.ProcessStatus(ctx, handle)
	case "process_output":
		handle, err := reqString(a, "process_handle")
		if err != nil { return nil, err }
		stdoutOffset, err := optInt(a, "stdout_offset", 0)
		if err != nil { return nil, err }
		stderrOffset, err := optInt(a, "stderr_offset", 0)
		if err != nil { return nil, err }
		maxBytes, err := optInt(a, "max_bytes", 16384)
		if err != nil { return nil, err }
		return f.ProcessOutput(ctx, handle, stdoutOffset, stderrOffset, maxBytes)
	case "process_input":
		handle, err := reqString(a, "process_handle")
		if err != nil { return nil, err }
		input, err := reqStringAllowEmpty(a, "input_text")
		if err != nil { return nil, err }
		newline, err := optBool(a, "append_newline", true)
		if err != nil { return nil, err }
		return f.ProcessInput(ctx, handle, input, newline)
	case "terminate_process":
		handle, err := reqString(a, "process_handle")
		if err != nil { return nil, err }
		grace, err := optFloat(a, "grace_s", 2)
		if err != nil { return nil, err }
		return f.TerminateProcess(ctx, handle, grace)
	case "release_process":
		handle, err := reqString(a, "process_handle")
		if err != nil { return nil, err }
		return f.ReleaseProcess(ctx, handle)
	default:
		return nil, fmt.Errorf("unknown public tool: %s", name)
	}
}

func reqString(a map[string]any, key string) (string, error) {
	value, ok := a[key].(string)
	if !ok || strings.TrimSpace(value) == "" {
		return "", argError(key, errors.New("must be a non-empty string"))
	}
	return value, nil
}

func reqStringAllowEmpty(a map[string]any, key string) (string, error) {
	value, ok := a[key].(string)
	if !ok {
		return "", argError(key, errors.New("must be a string"))
	}
	return value, nil
}

func reqStringSlice(a map[string]any, key string) ([]string, error) {
	raw, ok := a[key].([]any)
	if !ok || len(raw) == 0 {
		return nil, argError(key, errors.New("must be a non-empty string array"))
	}
	out := make([]string, len(raw))
	for i, item := range raw {
		value, ok := item.(string)
		if !ok || value == "" {
			return nil, argError(key, fmt.Errorf("item %d must be non-empty string", i))
		}
		out[i] = value
	}
	return out, nil
}

func optString(a map[string]any, key, def string) string {
	if raw, ok := a[key].(string); ok {
		return raw
	}
	return def
}

func optBool(a map[string]any, key string, def bool) (bool, error) {
	raw, exists := a[key]
	if !exists {
		return def, nil
	}
	value, ok := raw.(bool)
	if !ok {
		return false, argError(key, errors.New("must be boolean"))
	}
	return value, nil
}

func optInt(a map[string]any, key string, def int) (int, error) {
	v, err := optInt64(a, key, int64(def))
	if err != nil { return 0, err }
	if int64(int(v)) != v {
		return 0, argError(key, errors.New("integer outside platform range"))
	}
	return int(v), nil
}

func optInt64(a map[string]any, key string, def int64) (int64, error) {
	raw, exists := a[key]
	if !exists {
		return def, nil
	}
	value, err := exactJSONInt64(raw)
	if err != nil {
		return 0, argError(key, err)
	}
	return value, nil
}

func exactJSONInt64(raw any) (int64, error) {
	switch v := raw.(type) {
	case json.Number:
		value, err := v.Int64()
		if err != nil { return 0, errors.New("must be an integer") }
		return value, nil
	case int:
		return int64(v), nil
	case int64:
		return v, nil
	case float64:
		value := int64(v)
		if float64(value) != v {
			return 0, errors.New("must be an integer")
		}
		return value, nil
	default:
		return 0, errors.New("must be an integer")
	}
}

func optFloat(a map[string]any, key string, def float64) (float64, error) {
	raw, exists := a[key]
	if !exists {
		return def, nil
	}
	switch v := raw.(type) {
	case json.Number:
		value, err := v.Float64()
		if err != nil { return 0, argError(key, errors.New("must be numeric")) }
		return value, nil
	case float64:
		return v, nil
	case int:
		return float64(v), nil
	case int64:
		return float64(v), nil
	default:
		return 0, argError(key, errors.New("must be numeric"))
	}
}

func argError(key string, err error) error {
	return fmt.Errorf("%s: %w", key, err)
}

func applyDefault(a map[string]any, key string, value any) {
	if _, exists := a[key]; !exists {
		a[key] = value
	}
}

func inputSchema(name string) map[string]any {
	stringProp := func() map[string]any { return map[string]any{"type":"string"} }
	nonEmptyString := func() map[string]any { return map[string]any{"type":"string","minLength":1} }
	integer := func(min int) map[string]any { return map[string]any{"type":"integer","minimum":min} }
	number := func(min float64) map[string]any { return map[string]any{"type":"number","minimum":min} }
	boolean := func() map[string]any { return map[string]any{"type":"boolean"} }
	argv := map[string]any{
		"type":"array",
		"minItems":1,
		"items":map[string]any{"type":"string","minLength":1},
	}
	obj := func(props map[string]any, required ...string) map[string]any {
		out := map[string]any{
			"type":"object",
			"properties":props,
			"additionalProperties":false,
		}
		if len(required) > 0 { out["required"] = required }
		return out
	}
	switch name {
	case "computer_info", "list_processes":
		return obj(map[string]any{})
	case "read_file":
		return obj(map[string]any{"path":nonEmptyString(),"encoding":nonEmptyString()},"path")
	case "read_bytes":
		return obj(map[string]any{"path":nonEmptyString(),"offset":integer(0),"max_bytes":integer(1),"expected_file_version":nonEmptyString()},"path")
	case "stat_path":
		return obj(map[string]any{"path":nonEmptyString()},"path")
	case "list_directory":
		return obj(map[string]any{"path":nonEmptyString(),"offset":integer(0),"max_entries":integer(1)},"path")
	case "search_files":
		return obj(map[string]any{"root":nonEmptyString(),"query":nonEmptyString(),"offset":integer(0),"max_results":integer(1),"max_entries":integer(1),"max_depth":integer(0),"case_sensitive":boolean()},"root","query")
	case "search_content":
		return obj(map[string]any{"root":nonEmptyString(),"query":nonEmptyString(),"file_pattern":nonEmptyString(),"offset":integer(0),"max_results":integer(1),"max_entries":integer(1),"max_depth":integer(0),"max_total_bytes":integer(1),"case_sensitive":boolean()},"root","query")
	case "write_file", "append_file":
		return obj(map[string]any{"path":nonEmptyString(),"content":stringProp(),"encoding":nonEmptyString()},"path","content")
	case "make_directory":
		return obj(map[string]any{"path":nonEmptyString(),"parents":boolean()},"path")
	case "move_path":
		return obj(map[string]any{"source":nonEmptyString(),"destination":nonEmptyString()},"source","destination")
	case "replace_text":
		return obj(map[string]any{"path":nonEmptyString(),"old_string":stringProp(),"new_string":stringProp(),"expected_count":integer(0),"encoding":nonEmptyString()},"path","old_string","new_string")
	case "run_process":
		return obj(map[string]any{"argv":argv,"cwd":nonEmptyString(),"timeout_s":number(0.01)},"argv","cwd")
	case "start_process":
		return obj(map[string]any{"argv":argv,"cwd":nonEmptyString(),"max_runtime_s":number(0.01)},"argv","cwd")
	case "process_status", "release_process":
		return obj(map[string]any{"process_handle":nonEmptyString()},"process_handle")
	case "process_output":
		return obj(map[string]any{"process_handle":nonEmptyString(),"stdout_offset":integer(0),"stderr_offset":integer(0),"max_bytes":integer(1)},"process_handle")
	case "process_input":
		return obj(map[string]any{"process_handle":nonEmptyString(),"input_text":stringProp(),"append_newline":boolean()},"process_handle","input_text")
	case "terminate_process":
		return obj(map[string]any{"process_handle":nonEmptyString(),"grace_s":number(0)},"process_handle")
	default:
		panic("missing public tool schema: "+name)
	}
}
