package gateway

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

type fakeVeraPortSession struct {
	binding  veraport.SessionBinding
	response map[string]any
	err      error
	closed   bool
	requests []map[string]any
}

func (f *fakeVeraPortSession) Binding() veraport.SessionBinding { return f.binding }

func (f *fakeVeraPortSession) Request(_ context.Context, request map[string]any) (map[string]any, error) {
	copy := make(map[string]any, len(request))
	for k, v := range request {
		copy[k] = v
	}
	f.requests = append(f.requests, copy)
	if f.err != nil {
		return nil, f.err
	}
	if f.response != nil {
		return f.response, nil
	}
	switch request["operation"] {
	case "lane.list":
		return map[string]any{"ok": true, "result": map[string]any{"lanes": []any{}}}, nil
	case "lane.open":
		return map[string]any{"ok": true, "result": map[string]any{"fencing_token": int64(41)}}, nil
	case "lane.close":
		return map[string]any{"ok": true, "result": map[string]any{"closed": true}}, nil
	default:
		return map[string]any{"ok": true, "result": map[string]any{"operation": request["operation"]}}, nil
	}
}

func (f *fakeVeraPortSession) Close() error {
	f.closed = true
	return nil
}

func testController(now *time.Time, sessions ...*fakeVeraPortSession) *Controller {
	index := 0
	return &Controller{
		cfg: &Config{
			Endpoints: []Endpoint{{
				EndpointID:     "direct",
				Mode:           "DIRECT_STREAM",
				Host:           "127.0.0.1",
				Port:           17444,
				ServerHostname: "veraport.local",
			}},
		},
		creds: &veraport.ClientCredentials{},
		now:   func() time.Time { return *now },
		dial: func(context.Context, veraport.ClientConfig) (veraportSession, error) {
			if index >= len(sessions) {
				return nil, errors.New("unexpected extra dial")
			}
			s := sessions[index]
			index++
			return s, nil
		},
	}
}

func passingSession(expiry int64) *fakeVeraPortSession {
	return &fakeVeraPortSession{
		binding: veraport.SessionBinding{ExpiresAtMS: expiry},
	}
}

func TestEnsureClientRequiresLaneListBeforeCaching(t *testing.T) {
	now := time.UnixMilli(1000)
	session := passingSession(2000)
	controller := testController(&now, session)

	got, endpoint, err := controller.ensureClient(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got != session || endpoint.EndpointID != "direct" {
		t.Fatalf("unexpected qualified session: got=%v endpoint=%#v", got, endpoint)
	}
	if len(session.requests) != 1 || session.requests[0]["operation"] != "lane.list" {
		t.Fatalf("connection cached without lane.list probe: %#v", session.requests)
	}

	if _, _, err := controller.ensureClient(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(session.requests) != 1 {
		t.Fatalf("healthy cached session was reprobed: %#v", session.requests)
	}
}

func TestEnsureClientClosesExpiredCacheAndRequalifies(t *testing.T) {
	now := time.UnixMilli(1000)
	first := passingSession(1500)
	second := passingSession(3000)
	controller := testController(&now, first, second)

	if _, _, err := controller.ensureClient(context.Background()); err != nil {
		t.Fatal(err)
	}
	now = time.UnixMilli(1500)
	got, _, err := controller.ensureClient(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !first.closed {
		t.Fatal("expired cached session was not closed")
	}
	if got != second || len(second.requests) != 1 || second.requests[0]["operation"] != "lane.list" {
		t.Fatalf("replacement session was not independently qualified: %#v", second.requests)
	}
}

func TestEnsureClientRejectsFailedLaneListProbe(t *testing.T) {
	now := time.UnixMilli(1000)
	bad := passingSession(2000)
	bad.response = map[string]any{
		"ok": false,
		"error": map[string]any{"code": "DENIED", "message": "probe rejected"},
	}
	controller := testController(&now, bad)

	if _, _, err := controller.ensureClient(context.Background()); err == nil {
		t.Fatal("lane.list rejection was cached as a live endpoint")
	}
	if !bad.closed {
		t.Fatal("failed probe session was not closed")
	}
	if len(controller.paths) != 0 {
		t.Fatal("failed probe session remained cached")
	}
}

func TestControllerKeepsBothPathsAndPrefersDirect(t *testing.T) {
	now := time.UnixMilli(1000)
	direct := passingSession(5000)
	direct.binding.SessionID = "direct-session"
	edge := passingSession(5000)
	edge.binding.SessionID = "edge-session"
	controller := &Controller{
		cfg: &Config{
			MaxPathAgeMS: 5000,
			Endpoints: []Endpoint{
				{EndpointID: "direct", Mode: "DIRECT_STREAM", Host: "127.0.0.1", Port: 17444, ServerHostname: "veraport.local"},
				{EndpointID: "edge", Mode: "EDGE_STREAM", Host: "100.64.0.1", Port: 17444, ServerHostname: "veraport.local"},
			},
		},
		creds: &veraport.ClientCredentials{},
		now: func() time.Time { return now },
		paths: map[string]*livePath{},
		readLanes: map[string]*logicalReadLane{},
	}
	controller.dial = func(_ context.Context, cfg veraport.ClientConfig) (veraportSession, error) {
		if cfg.Host == "127.0.0.1" {
			return direct, nil
		}
		return edge, nil
	}
	paths, err := controller.ensurePaths(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) != 2 || paths[0].endpoint.EndpointID != "direct" || paths[1].endpoint.EndpointID != "edge" {
		t.Fatalf("semantic path ordering mismatch: %#v", paths)
	}
	if len(direct.requests) != 1 || len(edge.requests) != 1 {
		t.Fatalf("both paths were not independently probed: direct=%#v edge=%#v", direct.requests, edge.requests)
	}
}

func TestLogicalReadFailsOverWithIndependentMirrorFence(t *testing.T) {
	now := time.UnixMilli(1000)
	direct := passingSession(5000)
	direct.binding.SessionID = "direct-session"
	edge := passingSession(5000)
	edge.binding.SessionID = "edge-session"
	controller := &Controller{
		cfg: &Config{
			RequestedCapabilities: []string{"fs.read"},
			GatewayOperations: []string{"lane.open", "lane.close", "fs.read_text"},
			MaxPathAgeMS: 5000,
			Endpoints: []Endpoint{
				{EndpointID: "direct", Mode: "DIRECT_STREAM", Host: "127.0.0.1", Port: 17444, ServerHostname: "veraport.local"},
				{EndpointID: "edge", Mode: "EDGE_STREAM", Host: "100.64.0.1", Port: 17444, ServerHostname: "veraport.local"},
			},
		},
		creds: &veraport.ClientCredentials{},
		now: func() time.Time { return now },
		paths: map[string]*livePath{},
		readLanes: map[string]*logicalReadLane{},
	}
	controller.dial = func(_ context.Context, cfg veraport.ClientConfig) (veraportSession, error) {
		if cfg.Host == "127.0.0.1" {
			return direct, nil
		}
		return edge, nil
	}
	opened, err := controller.Call(context.Background(), "lane.open", map[string]any{
		"lane_id": "logical",
		"task_id": "read",
		"capabilities": []string{"fs.read"},
		"claims": []map[string]any{{"key": "fs:/tmp/file", "mode": "read"}},
		"ttl_s": 60.0,
	})
	if err != nil {
		t.Fatal(err)
	}
	result := opened["result"].(map[string]any)
	logicalFence, ok := exactInt64(result["fencing_token"])
	if !ok {
		t.Fatalf("logical fence missing: %#v", opened)
	}

	// The logical lane is initially materialized only on the preferred direct path.
	var directOpenFence int64
	for _, call := range direct.requests {
		if call["operation"] == "lane.open" {
			directOpenFence, _ = exactInt64(int64(41))
		}
	}
	if directOpenFence != 41 {
		t.Fatal("direct mirror was not materialized")
	}

	direct.err = errors.New("direct stream lost")
	response, err := controller.Call(context.Background(), "fs.read_text", map[string]any{
		"lane_id": "logical",
		"fencing_token": logicalFence,
		"path": "/tmp/file",
		"encoding": "utf-8",
	})
	if err != nil {
		t.Fatal(err)
	}
	if ok, _ := response["ok"].(bool); !ok {
		t.Fatalf("edge read did not succeed: %#v", response)
	}
	if !direct.closed {
		t.Fatal("failed direct path was not invalidated")
	}

	var edgeOpened, edgeRead bool
	for _, call := range edge.requests {
		switch call["operation"] {
		case "lane.open":
			edgeOpened = true
		case "fs.read_text":
			edgeRead = true
			if fence, ok := exactInt64(call["fencing_token"]); !ok || fence != 41 {
				t.Fatalf("edge read did not use edge mirror fence: %#v", call)
			}
		}
	}
	if !edgeOpened || !edgeRead {
		t.Fatalf("read did not rematerialize/fail over on edge: %#v", edge.requests)
	}
}

func TestMutationTransportFailureIsNotReplayedToEdge(t *testing.T) {
	now := time.UnixMilli(1000)
	direct := passingSession(5000)
	direct.binding.SessionID = "direct-session"
	edge := passingSession(5000)
	edge.binding.SessionID = "edge-session"
	controller := &Controller{
		cfg: &Config{
			RequestedCapabilities: []string{"fs.write"},
			GatewayOperations: []string{"fs.write_text"},
			MaxPathAgeMS: 5000,
			Endpoints: []Endpoint{
				{EndpointID: "direct", Mode: "DIRECT_STREAM", Host: "127.0.0.1", Port: 17444, ServerHostname: "veraport.local"},
				{EndpointID: "edge", Mode: "EDGE_STREAM", Host: "100.64.0.1", Port: 17444, ServerHostname: "veraport.local"},
			},
		},
		creds: &veraport.ClientCredentials{},
		now: func() time.Time { return now },
		paths: map[string]*livePath{},
		readLanes: map[string]*logicalReadLane{},
	}
	controller.dial = func(_ context.Context, cfg veraport.ClientConfig) (veraportSession, error) {
		if cfg.Host == "127.0.0.1" {
			return direct, nil
		}
		return edge, nil
	}
	if _, err := controller.ensurePaths(context.Background()); err != nil {
		t.Fatal(err)
	}
	direct.err = errors.New("ambiguous write transport failure")
	if _, err := controller.Call(context.Background(), "fs.write_text", map[string]any{"path": "/tmp/x", "content": "x"}); err == nil {
		t.Fatal("ambiguous write transport failure was hidden")
	}
	for _, call := range edge.requests {
		if call["operation"] == "fs.write_text" {
			t.Fatal("write was transparently replayed to edge")
		}
	}
}
