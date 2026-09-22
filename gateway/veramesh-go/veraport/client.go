package veraport

import (
	"context"
	"crypto/ecdsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"sync"
	"time"
)

type ClientCredentials struct {
	RootCAs              *x509.CertPool
	ControllerPrivateKey *ecdsa.PrivateKey
	WorkstationPublicKey *ecdsa.PublicKey
}

type ClientConfig struct {
	Host                  string
	Port                  int
	ServerName            string
	RootCAs               *x509.CertPool
	ControllerPrivateKey  *ecdsa.PrivateKey
	WorkstationPublicKey  *ecdsa.PublicKey
	RequestedCapabilities []string
	ConnectTimeout        time.Duration
	RequestTimeout        time.Duration
	MaxFrameBytes         int
}

type Client struct {
	conn           *tls.Conn
	binding        SessionBinding
	requestTimeout time.Duration
	maxFrameBytes  int
	mu             sync.Mutex
}

func Dial(ctx context.Context, cfg ClientConfig) (*Client, error) {
	if cfg.Host == "" || cfg.ServerName == "" {
		return nil, errors.New("host and server name are required")
	}
	if cfg.Port < 1 || cfg.Port > 65535 {
		return nil, errors.New("port outside 1..65535")
	}
	if cfg.RootCAs == nil || cfg.ControllerPrivateKey == nil ||
		cfg.WorkstationPublicKey == nil {
		return nil, errors.New("CA, controller key, and workstation key are required")
	}
	if cfg.ConnectTimeout <= 0 {
		cfg.ConnectTimeout = 5 * time.Second
	}
	if cfg.RequestTimeout <= 0 {
		cfg.RequestTimeout = 5 * time.Second
	}
	if cfg.MaxFrameBytes <= 0 {
		cfg.MaxFrameBytes = DefaultMaxFrameBytes
	}

	dialer := &net.Dialer{Timeout: cfg.ConnectTimeout}
	raw, err := dialer.DialContext(
		ctx,
		"tcp",
		net.JoinHostPort(cfg.Host, strconv.Itoa(cfg.Port)),
	)
	if err != nil {
		return nil, fmt.Errorf("dial VeraPort: %w", err)
	}
	conn := tls.Client(raw, &tls.Config{
		MinVersion: tls.VersionTLS13,
		ServerName: cfg.ServerName,
		RootCAs:    cfg.RootCAs,
		NextProtos: []string{ALPN},
	})
	deadline := time.Now().Add(cfg.ConnectTimeout)
	if err := conn.SetDeadline(deadline); err != nil {
		raw.Close()
		return nil, err
	}
	if err := conn.HandshakeContext(ctx); err != nil {
		raw.Close()
		return nil, fmt.Errorf("VeraPort TLS handshake failed: %w", err)
	}
	state := conn.ConnectionState()
	if state.Version != tls.VersionTLS13 || state.NegotiatedProtocol != ALPN {
		conn.Close()
		return nil, errors.New("VeraPort requires TLS 1.3 and veraport/1 ALPN")
	}

	var challenge ServerChallenge
	if err := ReadFrame(conn, &challenge, cfg.MaxFrameBytes); err != nil {
		conn.Close()
		return nil, err
	}
	expectedPrincipal, err := PrincipalID(cfg.WorkstationPublicKey, "workstation")
	if err != nil {
		conn.Close()
		return nil, err
	}
	expectedKeyID, err := KeyID(cfg.WorkstationPublicKey)
	if err != nil {
		conn.Close()
		return nil, err
	}
	if challenge.WorkstationPrincipal != expectedPrincipal ||
		challenge.WorkstationKeyID != expectedKeyID {
		conn.Close()
		return nil, errors.New("server challenge does not match pinned workstation key")
	}

	auth, err := CreateClientAuth(
		cfg.ControllerPrivateKey,
		challenge,
		cfg.RequestedCapabilities,
		nil,
	)
	if err != nil {
		conn.Close()
		return nil, err
	}
	if err := WriteFrame(conn, auth, cfg.MaxFrameBytes); err != nil {
		conn.Close()
		return nil, err
	}
	var accept ServerAccept
	if err := ReadFrame(conn, &accept, cfg.MaxFrameBytes); err != nil {
		conn.Close()
		return nil, err
	}
	binding, err := VerifyServerAccept(
		challenge,
		auth,
		accept,
		cfg.WorkstationPublicKey,
		time.Now().UnixMilli(),
	)
	if err != nil {
		conn.Close()
		return nil, err
	}
	if err := conn.SetDeadline(time.Time{}); err != nil {
		conn.Close()
		return nil, err
	}
	return &Client{
		conn:           conn,
		binding:        *binding,
		requestTimeout: cfg.RequestTimeout,
		maxFrameBytes:  cfg.MaxFrameBytes,
	}, nil
}

func (c *Client) Binding() SessionBinding {
	return c.binding
}

func (c *Client) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return nil
	}
	err := c.conn.Close()
	c.conn = nil
	return err
}

func (c *Client) Request(ctx context.Context, request map[string]any) (map[string]any, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return nil, errors.New("VeraPort client is closed")
	}
	requestID, ok := request["request_id"].(string)
	if !ok || requestID == "" {
		return nil, errors.New("request_id is required")
	}
	if request["protocol_version"] != ProtocolVersion {
		return nil, errors.New("protocol_version must be veraport-v1")
	}
	deadline := time.Now().Add(c.requestTimeout)
	if dl, ok := ctx.Deadline(); ok && dl.Before(deadline) {
		deadline = dl
	}
	if err := c.conn.SetDeadline(deadline); err != nil {
		return nil, err
	}
	defer c.conn.SetDeadline(time.Time{})

	if err := WriteFrame(c.conn, request, c.maxFrameBytes); err != nil {
		return nil, err
	}
	var response map[string]any
	if err := ReadFrame(c.conn, &response, c.maxFrameBytes); err != nil {
		return nil, err
	}
	got, ok := response["request_id"].(string)
	if !ok || got != requestID {
		encoded, _ := json.Marshal(response["request_id"])
		return nil, fmt.Errorf(
			"response request_id mismatch: expected %q got %s",
			requestID,
			encoded,
		)
	}
	return response, nil
}
