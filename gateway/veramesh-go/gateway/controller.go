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

type Controller struct {
	cfg   *Config
	creds *veraport.ClientCredentials

	mu       sync.Mutex
	client   *veraport.Client
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
	return &Controller{cfg: cfg, creds: creds}, nil
}

func (c *Controller) Config() *Config { return c.cfg }

func (c *Controller) ensureClient(ctx context.Context) (*veraport.Client, *Endpoint, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.client != nil && c.endpoint != nil {
		return c.client, c.endpoint, nil
	}
	var failures []error
	for i := range c.cfg.Endpoints {
		ep := &c.cfg.Endpoints[i]
		client, err := veraport.Dial(ctx, veraport.ClientConfig{
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
			c.client = client
			c.endpoint = ep
			return client, ep, nil
		}
		failures = append(failures, fmt.Errorf("%s: %w", ep.EndpointID, err))
	}
	return nil, nil, errors.Join(failures...)
}

func (c *Controller) invalidate(client *veraport.Client) {
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
	info["observed_at_ms"] = time.Now().UnixMilli()
	return info
}

func newID(prefix string) string {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	return prefix + ":" + hex.EncodeToString(raw[:])
}
