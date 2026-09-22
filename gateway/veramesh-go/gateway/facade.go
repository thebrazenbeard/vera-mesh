package gateway

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"path"
	"sort"
	"strings"
	"sync"
)

type RemoteError struct {
	Code    string
	Message string
}

func (e *RemoteError) Error() string { return e.Code + ": " + e.Message }

type ProcessLease struct {
	PublicHandle  string
	InternalHandle string
	LaneID         string
	FencingToken   int64
	CWD            string
}

type ControllerAPI interface {
	Config() *Config
	Call(context.Context, string, map[string]any) (map[string]any, error)
	MachineInfo(context.Context) map[string]any
}

type Facade struct {
	controller ControllerAPI
	actorID    string
	actorTag   string

	mu        sync.RWMutex
	processes map[string]ProcessLease
}

func NewFacade(controller ControllerAPI, actorID string) (*Facade, error) {
	actorID = strings.TrimSpace(actorID)
	if controller == nil || actorID == "" {
		return nil, errors.New("controller and actor_id are required")
	}
	sum := sha256.Sum256([]byte(actorID))
	return &Facade{
		controller: controller,
		actorID: actorID,
		actorTag: hex.EncodeToString(sum[:])[:16],
		processes: map[string]ProcessLease{},
	}, nil
}

func (f *Facade) newID(prefix string) string {
	return prefix + ":" + f.actorTag + ":" + strings.TrimPrefix(newID("id"), "id:")
}

func CanonicalRemotePath(value string) (string, error) {
	raw := strings.TrimSpace(value)
	if raw == "" {
		return "", errors.New("path must be a non-empty absolute string")
	}
	raw = strings.ReplaceAll(raw, "\\", "/")
	if len(raw) >= 3 && raw[1] == ':' && raw[2] == '/' {
		clean := path.Clean(raw[2:])
		if clean == "." || !strings.HasPrefix(clean, "/") {
			return "", errors.New("path must be absolute")
		}
		return raw[:2] + clean, nil
	}
	if strings.HasPrefix(raw, "//") {
		clean := path.Clean("/" + strings.TrimPrefix(raw, "//"))
		if clean == "/" {
			return "", errors.New("UNC path must name a host/share")
		}
		return "/" + clean, nil
	}
	clean := path.Clean(raw)
	if !strings.HasPrefix(clean, "/") {
		return "", errors.New("path must be absolute")
	}
	return clean, nil
}

func fsClaim(p, mode string) (map[string]any, error) {
	canon, err := CanonicalRemotePath(p)
	if err != nil { return nil, err }
	return map[string]any{"key":"fs:"+canon, "mode":mode}, nil
}

func cwdClaim(p, mode string) (map[string]any, error) {
	canon, err := CanonicalRemotePath(p)
	if err != nil { return nil, err }
	return map[string]any{"key":"cwd:"+canon, "mode":mode}, nil
}

func unwrap(response map[string]any) (map[string]any, error) {
	ok, _ := response["ok"].(bool)
	if !ok {
		code, message := "REMOTE_ERROR", "remote operation failed"
		if e, valid := response["error"].(map[string]any); valid {
			if v, ok := e["code"].(string); ok && v != "" { code = v }
			if v, ok := e["message"].(string); ok && v != "" { message = v }
		}
		return nil, &RemoteError{Code:code, Message:message}
	}
	result, ok := response["result"].(map[string]any)
	if !ok {
		return nil, errors.New("remote result must be an object")
	}
	return result, nil
}

func exactInt64(value any) (int64, bool) {
	switch v := value.(type) {
	case int:
		return int64(v), true
	case int64:
		return v, true
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) || math.Trunc(v) != v ||
			v < math.MinInt64 || v > math.MaxInt64 {
			return 0, false
		}
		return int64(v), true
	default:
		return 0, false
	}
}

func (f *Facade) openLane(ctx context.Context, capabilities []string, claims []map[string]any, ttlS float64, task string) (string, int64, error) {
	laneID := f.newID("public-lane")
	response, err := f.controller.Call(ctx, "lane.open", map[string]any{
		"lane_id": laneID,
		"task_id": f.newID(task),
		"capabilities": capabilities,
		"claims": claims,
		"ttl_s": ttlS,
	})
	if err != nil { return "", 0, err }
	result, err := unwrap(response)
	if err != nil { return "", 0, err }
	fence, ok := exactInt64(result["fencing_token"])
	if !ok || fence < 1 {
		return "", 0, errors.New("lane.open returned no fencing token")
	}
	return laneID, fence, nil
}

func (f *Facade) closeLane(ctx context.Context, laneID string, fence int64) map[string]any {
	response, err := f.controller.Call(ctx, "lane.close", map[string]any{
		"lane_id": laneID,
		"fencing_token": fence,
	})
	if err != nil {
		return map[string]any{"closed":false, "error":map[string]any{"code":"TRANSPORT_ERROR","message":err.Error()}}
	}
	if ok, _ := response["ok"].(bool); ok {
		return map[string]any{"closed":true}
	}
	return map[string]any{"closed":false, "error":response["error"]}
}

func (f *Facade) ephemeral(ctx context.Context, operation string, capabilities []string, claims []map[string]any, body map[string]any, task string) (result map[string]any, err error) {
	laneID, fence, err := f.openLane(ctx, capabilities, claims, 300, task)
	if err != nil { return nil, err }
	succeeded := false
	defer func() {
		cleanup := f.closeLane(context.Background(), laneID, fence)
		if succeeded && result != nil {
			if closed, _ := cleanup["closed"].(bool); !closed {
				result["_veramesh_cleanup"] = cleanup
			}
		}
	}()
	request := map[string]any{"lane_id":laneID, "fencing_token":fence}
	for key, value := range body { request[key] = value }
	response, err := f.controller.Call(ctx, operation, request)
	if err != nil { return nil, err }
	result, err = unwrap(response)
	if err != nil { return nil, err }
	succeeded = true
	return result, nil
}

func (f *Facade) ComputerInfo(ctx context.Context) (map[string]any, error) {
	return f.controller.MachineInfo(ctx), nil
}

func (f *Facade) ReadFile(ctx context.Context, p, encoding string) (map[string]any, error) {
	claim, err := fsClaim(p, "read"); if err != nil { return nil, err }
	if encoding == "" { encoding = "utf-8" }
	return f.ephemeral(ctx,"fs.read_text",[]string{"fs.read"},[]map[string]any{claim},map[string]any{"path":p,"encoding":encoding},"read-file")
}

func (f *Facade) ReadBytes(ctx context.Context, p string, offset int64, maxBytes any, expectedVersion any) (map[string]any, error) {
	claim, err := fsClaim(p, "read"); if err != nil { return nil, err }
	body:=map[string]any{"path":p,"offset":offset}
	if maxBytes != nil { body["max_bytes"]=maxBytes }
	if expectedVersion != nil { body["expected_file_version"]=expectedVersion }
	return f.ephemeral(ctx,"fs.read_bytes",[]string{"fs.read"},[]map[string]any{claim},body,"read-bytes")
}

func (f *Facade) StatPath(ctx context.Context, p string) (map[string]any, error) {
	claim, err := fsClaim(p, "read"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.stat",[]string{"fs.read"},[]map[string]any{claim},map[string]any{"path":p},"stat")
}

func (f *Facade) ListDirectory(ctx context.Context, p string, offset, maxEntries int) (map[string]any, error) {
	claim, err := fsClaim(p, "read"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.list_dir",[]string{"fs.read"},[]map[string]any{claim},map[string]any{"path":p,"offset":offset,"max_entries":maxEntries},"list-directory")
}

func (f *Facade) SearchFiles(ctx context.Context, body map[string]any) (map[string]any, error) {
	root, _ := body["root"].(string)
	claim, err := fsClaim(root, "read"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.search",[]string{"fs.read"},[]map[string]any{claim},body,"search-files")
}

func (f *Facade) SearchContent(ctx context.Context, body map[string]any) (map[string]any, error) {
	root, _ := body["root"].(string)
	claim, err := fsClaim(root, "read"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.search_content",[]string{"fs.read"},[]map[string]any{claim},body,"search-content")
}

func (f *Facade) WriteFile(ctx context.Context, p, content, encoding string) (map[string]any, error) {
	claim, err := fsClaim(p, "write"); if err != nil { return nil, err }
	if encoding == "" { encoding="utf-8" }
	return f.ephemeral(ctx,"fs.write_text",[]string{"fs.write"},[]map[string]any{claim},map[string]any{"path":p,"content":content,"encoding":encoding},"write-file")
}

func (f *Facade) AppendFile(ctx context.Context, p, content, encoding string) (map[string]any, error) {
	claim, err := fsClaim(p, "write"); if err != nil { return nil, err }
	if encoding == "" { encoding="utf-8" }
	return f.ephemeral(ctx,"fs.append_text",[]string{"fs.write"},[]map[string]any{claim},map[string]any{"path":p,"content":content,"encoding":encoding},"append-file")
}

func (f *Facade) MakeDirectory(ctx context.Context, p string, parents bool) (map[string]any, error) {
	claim, err := fsClaim(p, "write"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.mkdir",[]string{"fs.write"},[]map[string]any{claim},map[string]any{"path":p,"parents":parents},"mkdir")
}

func (f *Facade) MovePath(ctx context.Context, source, destination string) (map[string]any, error) {
	a, err := fsClaim(source,"write"); if err != nil { return nil, err }
	b, err := fsClaim(destination,"write"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.move",[]string{"fs.write"},[]map[string]any{a,b},map[string]any{"source":source,"destination":destination},"move")
}

func (f *Facade) ReplaceText(ctx context.Context, body map[string]any) (map[string]any, error) {
	p, _ := body["path"].(string)
	claim, err := fsClaim(p, "write"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"fs.replace_text",[]string{"fs.read","fs.write"},[]map[string]any{claim},body,"replace-text")
}

func (f *Facade) RunProcess(ctx context.Context, argv []string, cwd string, timeoutS float64) (map[string]any, error) {
	claim, err := cwdClaim(cwd,"write"); if err != nil { return nil, err }
	return f.ephemeral(ctx,"process.exec",[]string{"process.exec"},[]map[string]any{claim},map[string]any{"argv":argv,"cwd":cwd,"timeout_s":timeoutS},"run-process")
}

func (f *Facade) StartProcess(ctx context.Context, argv []string, cwd string, maxRuntimeS float64) (map[string]any, error) {
	claim, err := cwdClaim(cwd,"write"); if err != nil { return nil, err }
	ttl := maxRuntimeS + 300
	if ttl > 3600 { ttl = 3600 }
	laneID, fence, err := f.openLane(ctx, []string{"process.exec","process.inspect","process.interact","process.control"}, []map[string]any{claim}, ttl, "start-process")
	if err != nil { return nil, err }
	response, err := f.controller.Call(ctx,"process.start",map[string]any{
		"lane_id":laneID,"fencing_token":fence,"argv":argv,"cwd":cwd,"max_runtime_s":maxRuntimeS,
	})
	if err != nil { f.closeLane(context.Background(),laneID,fence); return nil, err }
	result, err := unwrap(response)
	if err != nil { f.closeLane(context.Background(),laneID,fence); return nil, err }
	internal, ok := result["process_handle"].(string)
	if !ok || internal == "" {
		f.closeLane(context.Background(),laneID,fence)
		return nil, errors.New("process.start returned no process handle")
	}
	canon, _ := CanonicalRemotePath(cwd)
	public := f.newID("job")
	f.mu.Lock()
	f.processes[public]=ProcessLease{PublicHandle:public,InternalHandle:internal,LaneID:laneID,FencingToken:fence,CWD:canon}
	f.mu.Unlock()
	result["process_handle"]=public
	return result,nil
}

func (f *Facade) lease(handle string) (ProcessLease, error) {
	f.mu.RLock(); defer f.mu.RUnlock()
	lease, ok := f.processes[handle]
	if !ok { return ProcessLease{}, fmt.Errorf("public process not found: %s", handle) }
	return lease,nil
}

func (f *Facade) processOperation(ctx context.Context, operation, handle string, body map[string]any) (map[string]any,error) {
	lease, err := f.lease(handle); if err != nil { return nil,err }
	request:=map[string]any{
		"lane_id":lease.LaneID,"fencing_token":lease.FencingToken,"process_handle":lease.InternalHandle,
	}
	for k,v:=range body { request[k]=v }
	response,err:=f.controller.Call(ctx,operation,request)
	if err!=nil { return nil,err }
	result,err:=unwrap(response)
	if err!=nil { return nil,err }
	result["process_handle"]=lease.PublicHandle
	return result,nil
}

func (f *Facade) ProcessStatus(ctx context.Context, handle string)(map[string]any,error){
	return f.processOperation(ctx,"process.status",handle,nil)
}
func (f *Facade) ProcessOutput(ctx context.Context, handle string, stdoutOffset,stderrOffset,maxBytes int)(map[string]any,error){
	return f.processOperation(ctx,"process.output",handle,map[string]any{"stdout_offset":stdoutOffset,"stderr_offset":stderrOffset,"max_bytes":maxBytes})
}
func (f *Facade) ProcessInput(ctx context.Context, handle,input string,appendNewline bool)(map[string]any,error){
	return f.processOperation(ctx,"process.input",handle,map[string]any{"input_text":input,"append_newline":appendNewline})
}
func (f *Facade) TerminateProcess(ctx context.Context, handle string, graceS float64)(map[string]any,error){
	return f.processOperation(ctx,"process.terminate",handle,map[string]any{"grace_s":graceS})
}

func (f *Facade) ReleaseProcess(ctx context.Context, handle string)(map[string]any,error){
	lease,err:=f.lease(handle); if err!=nil { return nil,err }
	cleanup:=f.closeLane(ctx,lease.LaneID,lease.FencingToken)
	closed,_:=cleanup["closed"].(bool)
	if closed {
		f.mu.Lock(); delete(f.processes,handle); f.mu.Unlock()
	}
	return map[string]any{"process_handle":handle,"released":closed,"cleanup":cleanup},nil
}

func (f *Facade) ListProcesses(ctx context.Context)(map[string]any,error){
	f.mu.RLock()
	handles:=make([]string,0,len(f.processes))
	for handle:=range f.processes { handles=append(handles,handle) }
	f.mu.RUnlock()
	sort.Strings(handles)
	results:=make([]map[string]any,0,len(handles))
	for _,handle:=range handles {
		status,err:=f.ProcessStatus(ctx,handle)
		if err!=nil {
			results=append(results,map[string]any{"process_handle":handle,"status_error":map[string]any{"code":"PROCESS_STATUS_ERROR","message":err.Error()}})
			continue
		}
		results=append(results,status)
	}
	return map[string]any{"processes":results},nil
}

func (f *Facade) Close(ctx context.Context) map[string]any {
	f.mu.RLock()
	handles:=make([]string,0,len(f.processes))
	for handle:=range f.processes { handles=append(handles,handle) }
	f.mu.RUnlock()
	sort.Strings(handles)
	results:=make([]map[string]any,0,len(handles))
	drained:=true
	for _,handle:=range handles {
		result,err:=f.ReleaseProcess(ctx,handle)
		if err!=nil {
			drained=false
			results=append(results,map[string]any{"process_handle":handle,"released":false,"error":err.Error()})
			continue
		}
		if released,_:=result["released"].(bool); !released { drained=false }
		results=append(results,result)
	}
	return map[string]any{"released_processes":results,"drained":drained}
}
