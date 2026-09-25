package gateway

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

type veraportSession interface {
	Binding() veraport.SessionBinding
	Request(context.Context, map[string]any) (map[string]any, error)
	Close() error
}

type veraportDial func(context.Context, veraport.ClientConfig) (veraportSession, error)

type livePath struct {
	client       veraportSession
	endpoint     *Endpoint
	observedAtMS int64
	rttMS        float64
}

type remoteMirror struct {
	sessionID string
	fence     int64
}

type logicalReadLane struct {
	laneID      string
	taskID      string
	claims      any
	fence       int64
	expiresAtMS int64
	mirrors     map[string]remoteMirror
}

type Controller struct {
	cfg   *Config
	creds *veraport.ClientCredentials
	dial  veraportDial
	now   func() time.Time

	mu    sync.Mutex
	paths map[string]*livePath

	laneMu     sync.Mutex
	readLanes  map[string]*logicalReadLane
	fenceEpoch uint32
	nextFence  uint32
}

const (
	logicalFenceCounterBits = 21
	logicalFenceMaxCounter  = (1 << logicalFenceCounterBits) - 1
	logicalFenceEpochMask   = (1 << 31) - 1
)


func NewController(cfg *Config) (*Controller, error) {
	if cfg == nil {
		return nil, errors.New("controller config is required")
	}
	creds, err := cfg.LoadVeraPortCredentials()
	if err != nil {
		return nil, err
	}
	fenceEpoch, err := newFenceEpoch()
	if err != nil {
		return nil, err
	}
	return &Controller{
		cfg:        cfg,
		creds:      creds,
		fenceEpoch: fenceEpoch,
		dial: func(ctx context.Context, cfg veraport.ClientConfig) (veraportSession, error) {
			return veraport.Dial(ctx, cfg)
		},
		now:       time.Now,
		paths:     map[string]*livePath{},
		readLanes: map[string]*logicalReadLane{},
	}, nil
}

func (c *Controller) Config() *Config { return c.cfg }

func (c *Controller) maxPathAgeMS() int64 {
	if c.cfg.MaxPathAgeMS <= 0 {
		return 5000
	}
	return int64(c.cfg.MaxPathAgeMS)
}

func pathModePriority(mode string) int {
	switch mode {
	case "DIRECT_STREAM":
		return 0
	case "EDGE_STREAM":
		return 1
	default:
		return 2
	}
}

func (c *Controller) orderedCurrentPathsLocked(nowMS int64) []*livePath {
	maxAge := c.maxPathAgeMS()
	out := make([]*livePath, 0, len(c.paths))
	for _, item := range c.paths {
		if item == nil || item.client == nil || item.endpoint == nil {
			continue
		}
		if nowMS >= item.client.Binding().ExpiresAtMS {
			continue
		}
		if item.observedAtMS > nowMS || nowMS-item.observedAtMS > maxAge {
			continue
		}
		out = append(out, item)
	}
	sort.Slice(out, func(i, j int) bool {
		pi := pathModePriority(out[i].endpoint.Mode)
		pj := pathModePriority(out[j].endpoint.Mode)
		if pi != pj {
			return pi < pj
		}
		if out[i].rttMS != out[j].rttMS {
			return out[i].rttMS < out[j].rttMS
		}
		return out[i].endpoint.EndpointID < out[j].endpoint.EndpointID
	})
	return out
}

func (c *Controller) ensurePaths(ctx context.Context) ([]*livePath, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.paths == nil {
		c.paths = map[string]*livePath{}
	}
	nowMS := c.now().UnixMilli()
	maxAge := c.maxPathAgeMS()
	var failures []error

	for i := range c.cfg.Endpoints {
		ep := &c.cfg.Endpoints[i]
		current := c.paths[ep.EndpointID]
		if current != nil {
			binding := current.client.Binding()
			switch {
			case nowMS >= binding.ExpiresAtMS:
				_ = current.client.Close()
				delete(c.paths, ep.EndpointID)
			case current.observedAtMS <= nowMS && nowMS-current.observedAtMS <= maxAge:
				continue
			default:
				rtt, err := qualifyDataPlane(ctx, current.client)
				if err == nil {
					current.observedAtMS = nowMS
					current.rttMS = rtt
					continue
				}
				_ = current.client.Close()
				delete(c.paths, ep.EndpointID)
				failures = append(failures, fmt.Errorf("%s stale-path qualification: %w", ep.EndpointID, err))
			}
		}

		client, err := c.dial(ctx, veraport.ClientConfig{
			Host:                  ep.Host,
			Port:                  ep.Port,
			ServerName:            ep.ServerHostname,
			RootCAs:               c.creds.RootCAs,
			ControllerPrivateKey:  c.creds.ControllerPrivateKey,
			WorkstationPublicKey:  c.creds.WorkstationPublicKey,
			RequestedCapabilities: c.cfg.RequestedCapabilities,
			ConnectTimeout:        c.cfg.ConnectTimeout(),
			RequestTimeout:        c.cfg.RequestTimeout(),
		})
		if err != nil {
			failures = append(failures, fmt.Errorf("%s: %w", ep.EndpointID, err))
			continue
		}
		rtt, err := qualifyDataPlane(ctx, client)
		if err != nil {
			_ = client.Close()
			failures = append(failures, fmt.Errorf("%s data-plane qualification: %w", ep.EndpointID, err))
			continue
		}
		c.paths[ep.EndpointID] = &livePath{
			client:       client,
			endpoint:     ep,
			observedAtMS: nowMS,
			rttMS:        rtt,
		}
	}

	paths := c.orderedCurrentPathsLocked(nowMS)
	if len(paths) == 0 {
		if len(failures) == 0 {
			return nil, errors.New("no current authenticated data-plane-verified VeraPort path")
		}
		return nil, errors.Join(failures...)
	}
	return append([]*livePath(nil), paths...), nil
}

func (c *Controller) ensureClient(ctx context.Context) (veraportSession, *Endpoint, error) {
	paths, err := c.ensurePaths(ctx)
	if err != nil {
		return nil, nil, err
	}
	return paths[0].client, paths[0].endpoint, nil
}

func qualifyDataPlane(ctx context.Context, client veraportSession) (float64, error) {
	started := time.Now()
	response, err := client.Request(ctx, map[string]any{
		"protocol_version": veraport.ProtocolVersion,
		"request_id":       newID("probe"),
		"operation":        "lane.list",
	})
	if err != nil {
		return 0, err
	}
	if ok, _ := response["ok"].(bool); !ok {
		if remote, valid := response["error"].(map[string]any); valid {
			code, _ := remote["code"].(string)
			message, _ := remote["message"].(string)
			return 0, fmt.Errorf("lane.list rejected: %s: %s", code, message)
		}
		return 0, errors.New("lane.list rejected")
	}
	if _, ok := response["result"].(map[string]any); !ok {
		return 0, errors.New("lane.list returned no result object")
	}
	return float64(time.Since(started).Microseconds()) / 1000.0, nil
}

func (c *Controller) invalidate(client veraportSession) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for id, item := range c.paths {
		if item != nil && item.client == client {
			_ = item.client.Close()
			delete(c.paths, id)
		}
	}
}

func (c *Controller) Close() error {
	c.mu.Lock()
	var errs []error
	for id, item := range c.paths {
		if item != nil && item.client != nil {
			if err := item.client.Close(); err != nil {
				errs = append(errs, err)
			}
		}
		delete(c.paths, id)
	}
	c.mu.Unlock()

	// Path/session shutdown and logical-lane bookkeeping deliberately use
	// non-nested locks. Read failover may invalidate a path while holding
	// laneMu, so nesting mu -> laneMu here would create a lock-order cycle.
	c.laneMu.Lock()
	c.readLanes = map[string]*logicalReadLane{}
	c.laneMu.Unlock()
	return errors.Join(errs...)
}

func (c *Controller) Call(ctx context.Context, operation string, body map[string]any) (map[string]any, error) {
	if _, ok := c.cfg.OperationSet()[operation]; !ok {
		return nil, fmt.Errorf("operation not enabled by controller config: %s", operation)
	}
	for _, reserved := range []string{"protocol_version", "request_id", "operation"} {
		if _, exists := body[reserved]; exists {
			return nil, fmt.Errorf("body attempts to override reserved field: %s", reserved)
		}
	}
	if operation == "lane.open" && isReadOnlyLaneOpen(body) {
		return c.openLogicalReadLane(ctx, body)
	}
	if laneID, fence, ok := logicalLaneRef(body); ok {
		switch operation {
		case "lane.renew":
			return c.renewLogicalReadLane(ctx, laneID, fence, body)
		case "lane.close":
			return c.closeLogicalReadLane(ctx, laneID, fence)
		case "fs.read_text", "fs.read_bytes", "fs.stat", "fs.list_dir", "fs.search", "fs.search_content":
			if c.hasLogicalReadLane(laneID, fence) {
				return c.callLogicalRead(ctx, operation, laneID, fence, body)
			}
		}
	}
	client, _, err := c.ensureClient(ctx)
	if err != nil {
		return nil, err
	}
	response, err := client.Request(ctx, requestFor(operation, body, "go"))
	if err != nil {
		// Never transparently replay a non-logical request. Reconnect/failover is
		// deferred to the next independent call so writes/process starts cannot duplicate.
		c.invalidate(client)
		return nil, err
	}
	return response, nil
}

func requestFor(operation string, body map[string]any, prefix string) map[string]any {
	request := map[string]any{
		"protocol_version": veraport.ProtocolVersion,
		"request_id":       newID(prefix),
		"operation":        operation,
	}
	for key, value := range body {
		if key == "protocol_version" || key == "request_id" || key == "operation" {
			continue
		}
		request[key] = value
	}
	return request
}

func stringSlice(value any) ([]string, bool) {
	switch v := value.(type) {
	case []string:
		return append([]string(nil), v...), true
	case []any:
		out := make([]string, 0, len(v))
		for _, item := range v {
			s, ok := item.(string)
			if !ok {
				return nil, false
			}
			out = append(out, s)
		}
		return out, true
	default:
		return nil, false
	}
}

func claimList(value any) ([]map[string]any, bool) {
	switch v := value.(type) {
	case []map[string]any:
		out := make([]map[string]any, len(v))
		for i := range v {
			out[i] = cloneMap(v[i])
		}
		return out, true
	case []any:
		out := make([]map[string]any, 0, len(v))
		for _, item := range v {
			m, ok := item.(map[string]any)
			if !ok {
				return nil, false
			}
			out = append(out, cloneMap(m))
		}
		return out, true
	default:
		return nil, false
	}
}

func isReadOnlyLaneOpen(body map[string]any) bool {
	caps, ok := stringSlice(body["capabilities"])
	if !ok || len(caps) != 1 || caps[0] != "fs.read" {
		return false
	}
	claims, ok := claimList(body["claims"])
	if !ok || len(claims) == 0 {
		return false
	}
	for _, claim := range claims {
		key, _ := claim["key"].(string)
		mode, _ := claim["mode"].(string)
		if mode != "read" || !strings.HasPrefix(key, "fs:") {
			return false
		}
	}
	return true
}

func logicalLaneRef(body map[string]any) (string, int64, bool) {
	laneID, ok := body["lane_id"].(string)
	if !ok || laneID == "" {
		return "", 0, false
	}
	fence, ok := exactInt64(body["fencing_token"])
	return laneID, fence, ok
}

func cloneMap(value map[string]any) map[string]any {
	out := make(map[string]any, len(value))
	for k, v := range value {
		out[k] = v
	}
	return out
}

func ttlSeconds(body map[string]any, fallback float64) (float64, bool) {
	value, exists := body["ttl_s"]
	if !exists || value == nil {
		return fallback, true
	}
	switch v := value.(type) {
	case float64:
		return v, v > 0
	case float32:
		return float64(v), v > 0
	case int:
		return float64(v), v > 0
	case int64:
		return float64(v), v > 0
	default:
		return 0, false
	}
}

func newFenceEpoch() (uint32, error) {
	var raw [4]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return 0, fmt.Errorf("logical fence epoch: %w", err)
	}
	epoch := binary.BigEndian.Uint32(raw[:]) & logicalFenceEpochMask
	if epoch == 0 {
		epoch = 1
	}
	return epoch, nil
}

func (c *Controller) allocateFenceLocked() (int64, error) {
	if c.fenceEpoch == 0 {
		epoch, err := newFenceEpoch()
		if err != nil {
			return 0, err
		}
		c.fenceEpoch = epoch
	}
	if c.nextFence >= logicalFenceMaxCounter {
		return 0, errors.New("logical read fence space exhausted for gateway incarnation")
	}
	c.nextFence++
	// Keep the externally visible token <= 2^52-1 so JSON/JavaScript clients
	// preserve it exactly, while a random 31-bit incarnation epoch prevents a
	// gateway restart from predictably reusing the previous process's fences.
	return int64((uint64(c.fenceEpoch) << logicalFenceCounterBits) | uint64(c.nextFence)), nil
}

func (c *Controller) openLogicalReadLane(ctx context.Context, body map[string]any) (map[string]any, error) {
	paths, err := c.ensurePaths(ctx)
	if err != nil {
		return nil, err
	}
	laneID, _ := body["lane_id"].(string)
	taskID, _ := body["task_id"].(string)
	if laneID == "" || taskID == "" {
		return nil, errors.New("lane_id and task_id are required")
	}
	ttl, ok := ttlSeconds(body, 300)
	if !ok {
		return nil, errors.New("ttl_s must be positive")
	}
	claims, ok := claimList(body["claims"])
	if !ok {
		return nil, errors.New("read lane claims are invalid")
	}

	c.laneMu.Lock()
	defer c.laneMu.Unlock()
	if c.readLanes == nil {
		c.readLanes = map[string]*logicalReadLane{}
	}
	if existing := c.readLanes[laneID]; existing != nil && c.now().UnixMilli() < existing.expiresAtMS {
		return nil, fmt.Errorf("logical read lane already active: %s", laneID)
	}
	fence, err := c.allocateFenceLocked()
	if err != nil {
		return nil, err
	}
	lane := &logicalReadLane{
		laneID:      laneID,
		taskID:      taskID,
		claims:      claims,
		fence:       fence,
		expiresAtMS: c.now().UnixMilli() + int64(ttl*1000),
		mirrors:     map[string]remoteMirror{},
	}
	var transportErrs []error
	for _, path := range paths {
		requestBody := cloneMap(body)
		response, reqErr := path.client.Request(ctx, requestFor("lane.open", requestBody, "logical-open"))
		if reqErr != nil {
			c.invalidate(path.client)
			transportErrs = append(transportErrs, fmt.Errorf("%s: %w", path.endpoint.EndpointID, reqErr))
			continue
		}
		if ok, _ := response["ok"].(bool); !ok {
			return response, nil
		}
		result, _ := response["result"].(map[string]any)
		remoteFence, valid := exactInt64(result["fencing_token"])
		if !valid || remoteFence < 1 {
			return nil, fmt.Errorf("%s lane.open returned invalid fencing token", path.endpoint.EndpointID)
		}
		lane.mirrors[path.endpoint.EndpointID] = remoteMirror{
			sessionID: path.client.Binding().SessionID,
			fence:     remoteFence,
		}
		c.readLanes[laneID] = lane
		return map[string]any{
			"ok": true,
			"result": map[string]any{
				"lane_id":           laneID,
				"fencing_token":     lane.fence,
				"logical_read_lane": true,
			},
		}, nil
	}
	return nil, errors.Join(transportErrs...)
}

func (c *Controller) requireLogicalReadLaneLocked(laneID string, fence int64) (*logicalReadLane, error) {
	lane := c.readLanes[laneID]
	if lane == nil {
		return nil, fmt.Errorf("logical read lane not found: %s", laneID)
	}
	if lane.fence != fence {
		return nil, fmt.Errorf("stale logical read fence: expected %d got %d", lane.fence, fence)
	}
	if c.now().UnixMilli() >= lane.expiresAtMS {
		delete(c.readLanes, laneID)
		return nil, fmt.Errorf("logical read lane expired: %s", laneID)
	}
	return lane, nil
}

func (c *Controller) hasLogicalReadLane(laneID string, fence int64) bool {
	c.laneMu.Lock()
	defer c.laneMu.Unlock()
	_, err := c.requireLogicalReadLaneLocked(laneID, fence)
	return err == nil
}

func (c *Controller) ensureMirror(ctx context.Context, lane *logicalReadLane, path *livePath) (int64, error) {
	binding := path.client.Binding()
	if mirror, ok := lane.mirrors[path.endpoint.EndpointID]; ok && mirror.sessionID == binding.SessionID {
		return mirror.fence, nil
	}
	remaining := lane.expiresAtMS - c.now().UnixMilli()
	if remaining <= 0 {
		return 0, fmt.Errorf("logical read lane expired: %s", lane.laneID)
	}
	response, err := path.client.Request(ctx, requestFor("lane.open", map[string]any{
		"lane_id":      lane.laneID,
		"task_id":      lane.taskID,
		"capabilities": []string{"fs.read"},
		"claims":       lane.claims,
		"ttl_s":        float64(remaining) / 1000.0,
	}, "logical-mirror"))
	if err != nil {
		c.invalidate(path.client)
		return 0, err
	}
	if ok, _ := response["ok"].(bool); !ok {
		return 0, fmt.Errorf("%s mirror lane.open rejected: %#v", path.endpoint.EndpointID, response["error"])
	}
	result, _ := response["result"].(map[string]any)
	fence, ok := exactInt64(result["fencing_token"])
	if !ok || fence < 1 {
		return 0, fmt.Errorf("%s mirror lane.open returned invalid fencing token", path.endpoint.EndpointID)
	}
	lane.mirrors[path.endpoint.EndpointID] = remoteMirror{sessionID: binding.SessionID, fence: fence}
	return fence, nil
}

func (c *Controller) callLogicalRead(ctx context.Context, operation, laneID string, fence int64, body map[string]any) (map[string]any, error) {
	paths, err := c.ensurePaths(ctx)
	if err != nil {
		return nil, err
	}
	c.laneMu.Lock()
	defer c.laneMu.Unlock()
	lane, err := c.requireLogicalReadLaneLocked(laneID, fence)
	if err != nil {
		return nil, err
	}
	var transportErrs []error
	for _, path := range paths {
		remoteFence, mirrorErr := c.ensureMirror(ctx, lane, path)
		if mirrorErr != nil {
			transportErrs = append(transportErrs, fmt.Errorf("%s: %w", path.endpoint.EndpointID, mirrorErr))
			continue
		}
		remoteBody := cloneMap(body)
		remoteBody["fencing_token"] = remoteFence
		response, reqErr := path.client.Request(ctx, requestFor(operation, remoteBody, "logical-read"))
		if reqErr != nil {
			c.invalidate(path.client)
			transportErrs = append(transportErrs, fmt.Errorf("%s: %w", path.endpoint.EndpointID, reqErr))
			continue
		}
		return response, nil
	}
	return nil, errors.Join(transportErrs...)
}

func (c *Controller) renewLogicalReadLane(ctx context.Context, laneID string, fence int64, body map[string]any) (map[string]any, error) {
	paths, err := c.ensurePaths(ctx)
	if err != nil {
		return nil, err
	}
	byID := map[string]*livePath{}
	for _, path := range paths {
		byID[path.endpoint.EndpointID] = path
	}
	ttl, ok := ttlSeconds(body, 300)
	if !ok {
		return nil, errors.New("ttl_s must be positive")
	}

	c.laneMu.Lock()
	defer c.laneMu.Unlock()
	lane, err := c.requireLogicalReadLaneLocked(laneID, fence)
	if err != nil {
		return nil, err
	}
	lane.expiresAtMS = c.now().UnixMilli() + int64(ttl*1000)
	for endpointID, mirror := range lane.mirrors {
		path := byID[endpointID]
		if path == nil || path.client.Binding().SessionID != mirror.sessionID {
			delete(lane.mirrors, endpointID)
			continue
		}
		response, reqErr := path.client.Request(ctx, requestFor("lane.renew", map[string]any{
			"lane_id":       laneID,
			"fencing_token": mirror.fence,
			"ttl_s":         ttl,
		}, "logical-renew"))
		if reqErr != nil {
			c.invalidate(path.client)
			delete(lane.mirrors, endpointID)
			continue
		}
		if ok, _ := response["ok"].(bool); !ok {
			delete(lane.mirrors, endpointID)
		}
	}
	return map[string]any{
		"ok": true,
		"result": map[string]any{
			"lane_id":           laneID,
			"fencing_token":     fence,
			"logical_read_lane": true,
		},
	}, nil
}

func (c *Controller) closeLogicalReadLane(ctx context.Context, laneID string, fence int64) (map[string]any, error) {
	paths, ensureErr := c.ensurePaths(ctx)
	byID := map[string]*livePath{}
	for _, path := range paths {
		byID[path.endpoint.EndpointID] = path
	}

	c.laneMu.Lock()
	defer c.laneMu.Unlock()
	lane, err := c.requireLogicalReadLaneLocked(laneID, fence)
	if err != nil {
		return nil, err
	}
	var unresolved []error
	for endpointID, mirror := range lane.mirrors {
		path := byID[endpointID]
		if path == nil || path.client.Binding().SessionID != mirror.sessionID {
			unresolved = append(unresolved, fmt.Errorf("%s mirror session unavailable", endpointID))
			continue
		}
		response, reqErr := path.client.Request(ctx, requestFor("lane.close", map[string]any{
			"lane_id":       laneID,
			"fencing_token": mirror.fence,
		}, "logical-close"))
		if reqErr != nil {
			c.invalidate(path.client)
			unresolved = append(unresolved, fmt.Errorf("%s: %w", endpointID, reqErr))
			continue
		}
		if ok, _ := response["ok"].(bool); !ok {
			unresolved = append(unresolved, fmt.Errorf("%s close rejected: %#v", endpointID, response["error"]))
			continue
		}
		delete(lane.mirrors, endpointID)
	}
	if ensureErr != nil && len(paths) == 0 {
		unresolved = append(unresolved, ensureErr)
	}
	if len(unresolved) != 0 {
		return nil, errors.Join(unresolved...)
	}
	delete(c.readLanes, laneID)
	return map[string]any{
		"ok": true,
		"result": map[string]any{
			"lane_id":           laneID,
			"closed":            true,
			"logical_read_lane": true,
		},
	}, nil
}

func (c *Controller) MachineInfo(ctx context.Context) map[string]any {
	info := map[string]any{
		"schema":                 "VERAMESH_GO_MACHINE_INFO_V1",
		"requested_capabilities": append([]string(nil), c.cfg.RequestedCapabilities...),
		"gateway_operations":     append([]string(nil), c.cfg.GatewayOperations...),
		"connected":              false,
	}
	paths, err := c.ensurePaths(ctx)
	if err != nil {
		info["path_error"] = err.Error()
		return info
	}
	selected := paths[0]
	binding := selected.client.Binding()
	info["connected"] = true
	info["endpoint_id"] = selected.endpoint.EndpointID
	info["selected_path_id"] = selected.endpoint.EndpointID
	info["path_mode"] = selected.endpoint.Mode
	info["workstation_principal"] = binding.WorkstationPrincipal
	info["controller_principal"] = binding.ControllerPrincipal
	info["session_expires_at_ms"] = binding.ExpiresAtMS
	info["observed_at_ms"] = c.now().UnixMilli()
	pathInfo := make([]map[string]any, 0, len(paths))
	for _, item := range paths {
		b := item.client.Binding()
		pathInfo = append(pathInfo, map[string]any{
			"path_id":                item.endpoint.EndpointID,
			"endpoint_id":            item.endpoint.EndpointID,
			"mode":                   item.endpoint.Mode,
			"session_id":             b.SessionID,
			"expires_at_ms":          b.ExpiresAtMS,
			"observed_at_ms":         item.observedAtMS,
			"rtt_ms":                 item.rttMS,
			"authenticated":          true,
			"data_plane_verified":    true,
			"durable_idempotency":    item.endpoint.DurableIdempotency,
			"granted_capabilities":   capabilityKeys(b.GrantedCapabilities),
		})
	}
	info["paths"] = pathInfo
	return info
}

func capabilityKeys(values map[string]struct{}) []string {
	out := make([]string, 0, len(values))
	for value := range values {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

func newID(prefix string) string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	return prefix + ":" + hex.EncodeToString(raw[:])
}
