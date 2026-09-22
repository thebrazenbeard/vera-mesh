package gateway

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
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

type Controller struct {
	cfg   *Config
	creds *veraport.ClientCredentials
	dial  veraportDial
	now   func() time.Time

	mu       sync.Mutex
	client   veraportSession
	endpoint *Endpoint
}

func NewController(cfg *Config) (*Controller, error) {
	if cfg == nil {
		return nil, errors.New("controller config is required")
	}
	creds, err := cfg.LoadVeraPortCredentials()
	if err != nil {
		return nil, err
	}
	return &Controller{
		cfg:   cfg,
		creds: creds,
		dial: func(ctx context.Context, cfg veraport.ClientConfig) (veraportSession, error) {
			return veraport.Dial(ctx, cfg)
		},
		now: time.Now,
	}, nil
}

func (c *Controller) Config() *Config { return c.cfg }

func (c *Controller) ensureClient(ctx context.Context) (veraportSession, *Endpoint, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.client != nil && c.endpoint != nil {
		if c.now().UnixMilli() < c.client.Binding().ExpiresAtMS {
			return c.client, c.endpoint, nil
		}
		_ = c.client.Close()
		c.client = nil
		c.endpoint = nil
	}
	var failures []error
	for i := range c.cfg.Endpoints {
		ep := &c.cfg.Endpoints[i]
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
		if err == nil {
			if err := qualifyDataPlane(ctx, client); err == nil {
				c.client = client
				c.endpoint = ep
				return client, ep, nil
			} else {
				_ = client.Close()
				failures = append(failures, fmt.Errorf("%s data-plane qualification: %w", ep.EndpointID, err))
				continue
			}
		}
		failures = append(failures, fmt.Errorf("%s: %w", ep.EndpointID, err))
	}
	return nil, nil, errors.Join(failures...)
}

func qualifyDataPlane(ctx context.Context, client veraportSession) error {
	response, err := client.Request(ctx, map[string]any{
		"protocol_version": veraport.ProtocolVersion,
		"request_id":       newID("probe"),
		"operation":        "lane.list",
	})
	if err != nil {
		return err
	}
	if ok, _ := response["ok"].(bool); !ok {
		if remote, valid := response["error"].(map[string]any); valid {
			code, _ := remote["code"].(string)
			message, _ := remote["message"].(string)
			return fmt.Errorf("lane.list rejected: %s: %s", code, message)
		}
		return errors.New("lane.list rejected")
	}
	if _, ok := response["result"].(map[string]any); !ok {
		return errors.New("lane.list returned no result object")
	}
	return nil
}

func (c *Controller) invalidate(client veraportSession) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.client == client {
		_ = c.client.Close()
		c.client = nil
		c.endpoint = nil
	}
}

func (c *Controller) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.client == nil {
		return nil
	}
	err := c.client.Close()
	c.client = nil
	c.endpoint = nil
	return err
}

func (c *Controller) Call(ctx context.Context, operation string, body map[string]any) (map[string]any, error) {
	if _, ok := c.cfg.OperationSet()[operation]; !ok {
		return nil, fmt.Errorf("operation not enabled by controller config: %s", operation)
	}
	client, _, err := c.ensureClient(ctx)
	if err != nil {
		return nil, err
	}
	request := map[string]any{
		"protocol_version": veraport.ProtocolVersion,
		"request_id":       newID("go"),
		"operation":        operation,
	}
	for key, value := range body {
		if key == "protocol_version" || key == "request_id" || key == "operation" {
			return nil, fmt.Errorf("body attempts to override reserved field: %s", key)
		}
		request[key] = value
	}
	response, err := client.Request(ctx, request)
	if err != nil {
		// Never transparently replay the failed request. Reconnect is deferred
		// until the next independent call so writes/process starts cannot duplicate.
		c.invalidate(client)
		return nil, err
	}
	return response, nil
}

func (c *Controller) MachineInfo(ctx context.Context) map[string]any {
	info := map[string]any{
		"schema":                 "VERAMESH_GO_MACHINE_INFO_V1",
		"requested_capabilities": append([]string(nil), c.cfg.RequestedCapabilities...),
		"gateway_operations":     append([]string(nil), c.cfg.GatewayOperations...),
		"connected":              false,
	}
	client, endpoint, err := c.ensureClient(ctx)
	if err != nil {
		info["path_error"] = err.Error()
		return info
	}
	binding := client.Binding()
	info["connected"] = true
	info["endpoint_id"] = endpoint.EndpointID
	info["path_mode"] = endpoint.Mode
	info["workstation_principal"] = binding.WorkstationPrincipal
	info["controller_principal"] = binding.ControllerPrincipal
	info["session_expires_at_ms"] = binding.ExpiresAtMS
	info["observed_at_ms"] = c.now().UnixMilli()
	return info
}

func newID(prefix string) string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	return prefix + ":" + hex.EncodeToString(raw[:])
}
