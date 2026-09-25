package gateway

import (
	"context"
	"testing"
)

type wideningWorkBridgeStub struct{}

func (w wideningWorkBridgeStub) SupportsPublic(string) bool { return true }
func (w wideningWorkBridgeStub) CanHandlePublic(string, map[string]any) bool { return true }
func (w wideningWorkBridgeStub) CallPublic(context.Context, string, map[string]any) (map[string]any, error) {
	return map[string]any{"backend":"workbridge"}, nil
}

func TestWorkBridgeCannotWidenVeraMeshPublicPolicy(t *testing.T) {
	controller := &Controller{cfg:&Config{
		RequestedCapabilities: []string{"fs.read"},
		GatewayOperations: []string{"lane.open","lane.close","fs.read_text"},
	}}
	gateway := &MCPGateway{controller:controller, workbridge:wideningWorkBridgeStub{}}
	if !gateway.supportsPolicy(PublicToolByName["read_file"]) {
		t.Fatal("already-authorized read_file unexpectedly unavailable")
	}
	if gateway.supportsPolicy(PublicToolByName["write_file"]) {
		t.Fatal("WorkBridge widened public policy to write_file")
	}
}
