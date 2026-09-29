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


func TestGrantedProcessRequiresOuterProcessCapabilityAndUpstreamTool(t *testing.T) {
	controller := &Controller{cfg:&Config{
		RequestedCapabilities: []string{"process.exec"},
		GatewayOperations: []string{"lane.open","lane.close"},
	}}
	gateway := &MCPGateway{controller:controller, workbridge:wideningWorkBridgeStub{}}
	if !gateway.supportsPolicy(PublicToolByName["run_granted_process"]) {
		t.Fatal("granted process unavailable despite explicit outer process capability and upstream tool")
	}
	controller.cfg.RequestedCapabilities = []string{"fs.read"}
	if gateway.supportsPolicy(PublicToolByName["run_granted_process"]) {
		t.Fatal("granted process widened outer process capability")
	}
}
