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
	return f.response, nil
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
		response: map[string]any{
			"ok": true,
			"result": map[string]any{"lanes": []any{}},
		},
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
	if controller.client != nil || controller.endpoint != nil {
		t.Fatal("failed probe session remained cached")
	}
}
