package main

import (
	"testing"

	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

func TestCurrentBuildInfoBindsRuntimeAndProtocol(t *testing.T) {
	oldVersion, oldCommit := buildVersion, buildCommit
	buildVersion, buildCommit = "0.1.0-0001", "0123456789abcdef"
	defer func() {
		buildVersion, buildCommit = oldVersion, oldCommit
	}()

	info := currentBuildInfo()
	if info.Schema != "VERAMESH_GATEWAY_BUILD_V1" {
		t.Fatalf("unexpected schema: %q", info.Schema)
	}
	if info.Version != "0.1.0-0001" || info.Commit != "0123456789abcdef" {
		t.Fatalf("build identity mismatch: %#v", info)
	}
	if info.ProtocolVersion != veraport.ProtocolVersion {
		t.Fatalf("protocol mismatch: %q", info.ProtocolVersion)
	}
	if info.GOOS == "" || info.GOARCH == "" {
		t.Fatalf("runtime target missing: %#v", info)
	}
}
