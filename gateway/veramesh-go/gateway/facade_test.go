package gateway

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

type fakeController struct {
	cfg     *Config
	closeOK bool
	calls   []fakeCall
}

type fakeCall struct {
	Operation string
	Body      map[string]any
}

func newFakeController() *fakeController {
	return &fakeController{
		closeOK: true,
		cfg: &Config{
			Schema: "VERAPORT_CONTROLLER_MCP_CONFIG_V1",
			RequestedCapabilities: []string{
				"fs.read", "fs.write",
				"process.exec", "process.inspect",
				"process.interact", "process.control",
			},
			GatewayOperations: []string{
				"lane.list", "lane.open", "lane.close",
				"fs.read_text", "fs.read_bytes", "fs.stat",
				"fs.list_dir", "fs.search", "fs.search_content",
				"fs.write_text", "fs.append_text", "fs.mkdir",
				"fs.move", "fs.replace_text",
				"process.exec", "process.start", "process.list",
				"process.status", "process.output", "process.input",
				"process.terminate",
			},
		},
	}
}

func (f *fakeController) Config() *Config { return f.cfg }

func cloneBody(body map[string]any) map[string]any {
	out := make(map[string]any, len(body))
	for k, v := range body {
		out[k] = v
	}
	return out
}

func (f *fakeController) Call(_ context.Context, operation string, body map[string]any) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{Operation: operation, Body: cloneBody(body)})
	switch operation {
	case "lane.open":
		return map[string]any{"ok": true, "result": map[string]any{
			"lane_id": body["lane_id"], "fencing_token": float64(41),
		}}, nil
	case "lane.close":
		if !f.closeOK {
			return map[string]any{"ok": false, "error": map[string]any{
				"code": "CLOSE_FAILED", "message": "cleanup failed",
			}}, nil
		}
		return map[string]any{"ok": true, "result": map[string]any{"closed": true}}, nil
	case "fs.read_text":
		return map[string]any{"ok": true, "result": map[string]any{"content": "hello"}}, nil
	case "fs.write_text":
		return map[string]any{"ok": true, "result": map[string]any{"written": true}}, nil
	case "fs.search_content":
		return map[string]any{"ok": true, "result": map[string]any{"matches": []any{}}}, nil
	case "process.start":
		return map[string]any{"ok": true, "result": map[string]any{
			"process_handle": "proc:internal", "running": true, "pid": float64(123),
		}}, nil
	case "process.status":
		return map[string]any{"ok": true, "result": map[string]any{
			"process_handle": body["process_handle"], "running": true,
		}}, nil
	case "process.output":
		return map[string]any{"ok": true, "result": map[string]any{
			"process_handle": body["process_handle"], "stdout": "ok", "stderr": "",
		}}, nil
	case "process.input":
		return map[string]any{"ok": true, "result": map[string]any{
			"process_handle": body["process_handle"], "bytes_written": float64(3),
		}}, nil
	case "process.terminate":
		return map[string]any{"ok": true, "result": map[string]any{
			"process_handle": body["process_handle"], "running": false,
		}}, nil
	default:
		return map[string]any{"ok": true, "result": map[string]any{"operation": operation}}, nil
	}
}

func (f *fakeController) MachineInfo(_ context.Context) map[string]any {
	return map[string]any{"schema": "VERAMESH_GO_MACHINE_INFO_V1"}
}

func TestCanonicalRemotePathMatchesPublicPolicy(t *testing.T) {
	cases := map[string]string{
		`C:\Users\Patrick\work\..\repo`: "C:/Users/Patrick/repo",
		"/srv/work/../repo": "/srv/repo",
		`\\server\share\a\..\b`: "//server/share/b",
	}
	for input, want := range cases {
		got, err := CanonicalRemotePath(input)
		if err != nil {
			t.Fatalf("%q: %v", input, err)
		}
		if got != want {
			t.Fatalf("%q => %q want %q", input, got, want)
		}
	}
	for _, invalid := range []string{"relative/path", "1:/not-a-drive", `\\\\server`, `\\\\server\\share\\..\\escape`, "C:/bad\x00name"} {
		if _, err := CanonicalRemotePath(invalid); err == nil {
			t.Fatalf("invalid path accepted: %q", invalid)
		}
	}
}

func TestExactInt64AcceptsJSONNumberWithoutPrecisionLoss(t *testing.T) {
	got, ok := exactInt64(json.Number("9007199254740993"))
	if !ok || got != 9007199254740993 {
		t.Fatalf("exact JSON integer rejected or changed: got=%d ok=%v", got, ok)
	}
	if _, ok := exactInt64(json.Number("1.5")); ok {
		t.Fatal("fractional JSON number accepted as int64")
	}
}

func TestReadFileHidesLaneAndClaimsExactPath(t *testing.T) {
	ctrl := newFakeController()
	facade, err := NewFacade(ctrl, "issuer|patrick")
	if err != nil {
		t.Fatal(err)
	}
	result, err := facade.ReadFile(context.Background(), `C:\Users\Patrick\repo\README.md`, "utf-8")
	if err != nil {
		t.Fatal(err)
	}
	if result["content"] != "hello" {
		t.Fatalf("unexpected result: %#v", result)
	}
	if len(ctrl.calls) != 3 ||
		ctrl.calls[0].Operation != "lane.open" ||
		ctrl.calls[1].Operation != "fs.read_text" ||
		ctrl.calls[2].Operation != "lane.close" {
		t.Fatalf("unexpected operation sequence: %#v", ctrl.calls)
	}
	if _, leaked := result["lane_id"]; leaked {
		t.Fatal("public result leaked lane_id")
	}
	if _, leaked := result["fencing_token"]; leaked {
		t.Fatal("public result leaked fencing_token")
	}
	claims, ok := ctrl.calls[0].Body["claims"].([]map[string]any)
	if !ok || len(claims) != 1 {
		t.Fatalf("unexpected claims: %#v", ctrl.calls[0].Body["claims"])
	}
	if claims[0]["key"] != "fs:C:/Users/Patrick/repo/README.md" ||
		claims[0]["mode"] != "read" {
		t.Fatalf("unexpected claim: %#v", claims[0])
	}
}

func TestSuccessfulMutationReportsCleanupFailureWithoutReplaySignal(t *testing.T) {
	ctrl := newFakeController()
	ctrl.closeOK = false
	facade, _ := NewFacade(ctrl, "issuer|patrick")
	result, err := facade.WriteFile(
		context.Background(),
		`C:\Users\Patrick\repo\x.txt`,
		"done",
		"utf-8",
	)
	if err != nil {
		t.Fatal(err)
	}
	if result["written"] != true {
		t.Fatalf("write result lost: %#v", result)
	}
	cleanup, ok := result["_veramesh_cleanup"].(map[string]any)
	if !ok || cleanup["closed"] != false {
		t.Fatalf("cleanup evidence missing: %#v", result)
	}
	var writes int
	for _, call := range ctrl.calls {
		if call.Operation == "fs.write_text" {
			writes++
		}
	}
	if writes != 1 {
		t.Fatalf("write replayed: %d calls", writes)
	}
}

func TestManagedProcessUsesActorBoundPublicHandle(t *testing.T) {
	ctrl := newFakeController()
	first, _ := NewFacade(ctrl, "issuer|patrick")
	second, _ := NewFacade(ctrl, "issuer|other")

	started, err := first.StartProcess(
		context.Background(),
		[]string{"python", "-c", "print(42)"},
		`C:\Users\Patrick\repo`,
		30,
	)
	if err != nil {
		t.Fatal(err)
	}
	handle, _ := started["process_handle"].(string)
	if !strings.HasPrefix(handle, "job:") || handle == "proc:internal" {
		t.Fatalf("bad public handle: %q", handle)
	}
	status, err := first.ProcessStatus(context.Background(), handle)
	if err != nil {
		t.Fatal(err)
	}
	if status["process_handle"] != handle {
		t.Fatalf("internal handle leaked: %#v", status)
	}
	if _, err := second.ProcessStatus(context.Background(), handle); err == nil {
		t.Fatal("second actor facade accepted first actor's process handle")
	}
	released, err := first.ReleaseProcess(context.Background(), handle)
	if err != nil {
		t.Fatal(err)
	}
	if released["released"] != true {
		t.Fatalf("process lease did not release: %#v", released)
	}
	if _, err := first.ProcessStatus(context.Background(), handle); err == nil {
		t.Fatal("released public handle remained usable")
	}
}

func TestSearchContentClaimsOnlySearchRoot(t *testing.T) {
	ctrl := newFakeController()
	facade, _ := NewFacade(ctrl, "issuer|patrick")
	_, err := facade.SearchContent(context.Background(), map[string]any{
		"root": `C:\Users\Patrick\repo`,
		"query": "needle",
		"file_pattern": "*.py",
	})
	if err != nil {
		t.Fatal(err)
	}
	if ctrl.calls[1].Operation != "fs.search_content" {
		t.Fatalf("wrong operation: %#v", ctrl.calls)
	}
	claims := ctrl.calls[0].Body["claims"].([]map[string]any)
	if claims[0]["key"] != "fs:C:/Users/Patrick/repo" || claims[0]["mode"] != "read" {
		t.Fatalf("wrong search claim: %#v", claims)
	}
}
