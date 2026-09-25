package gateway

import (
	"bytes"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/thebrazenbeard/vera-mesh/gateway/veramesh-go/veraport"
)

type Endpoint struct {
	EndpointID        string `json:"endpoint_id"`
	Mode              string `json:"mode"`
	Host              string `json:"host"`
	Port              int    `json:"port"`
	ServerHostname    string `json:"server_hostname"`
	DurableIdempotency bool  `json:"durable_idempotency"`
}

type Config struct {
	Schema                  string     `json:"schema"`
	ControllerKey           string     `json:"controller_key"`
	TLSCA                   string     `json:"tls_ca"`
	WorkstationPublicKey    string     `json:"workstation_public_key"`
	RequestedCapabilities   []string   `json:"requested_capabilities"`
	GatewayOperations       []string   `json:"gateway_operations"`
	Endpoints               []Endpoint `json:"endpoints"`
	MaxPathAgeMS            int        `json:"max_path_age_ms,omitempty"`
	ConnectTimeoutS         float64    `json:"connect_timeout_s,omitempty"`
	RequestTimeoutS         float64    `json:"request_timeout_s,omitempty"`

	baseDir string
}

var operationCapabilities = map[string][]string{
	"fs.read_text":       {"fs.read"},
	"fs.read_bytes":      {"fs.read"},
	"fs.stat":            {"fs.read"},
	"fs.list_dir":        {"fs.read"},
	"fs.search":          {"fs.read"},
	"fs.search_content":  {"fs.read"},
	"fs.write_text":      {"fs.write"},
	"fs.append_text":     {"fs.write"},
	"fs.mkdir":           {"fs.write"},
	"fs.move":            {"fs.write"},
	"fs.replace_text":    {"fs.read", "fs.write"},
	"process.exec":       {"process.exec"},
	"process.start":      {"process.exec"},
	"process.list":       {"process.inspect"},
	"process.status":     {"process.inspect"},
	"process.output":     {"process.inspect"},
	"process.input":      {"process.interact"},
	"process.terminate":  {"process.control"},
}

var allowedOperations = func() map[string]struct{} {
	out := map[string]struct{}{
		"lane.list": {}, "lane.open": {}, "lane.renew": {}, "lane.close": {},
	}
	for op := range operationCapabilities {
		out[op] = struct{}{}
	}
	return out
}()

func LoadConfig(filename string) (*Config, error) {
	raw, err := os.ReadFile(filename)
	if err != nil {
		return nil, fmt.Errorf("read controller config: %w", err)
	}
	var cfg Config
	dec := json.NewDecoder(bytesNewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&cfg); err != nil {
		return nil, fmt.Errorf("decode controller config: %w", err)
	}
	cfg.baseDir = filepath.Dir(filename)
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	cfg.ControllerKey = cfg.resolve(cfg.ControllerKey)
	cfg.TLSCA = cfg.resolve(cfg.TLSCA)
	cfg.WorkstationPublicKey = cfg.resolve(cfg.WorkstationPublicKey)
	return &cfg, nil
}

func (c *Config) Validate() error {
	if c.Schema != "VERAPORT_CONTROLLER_MCP_CONFIG_V1" {
		return errors.New("wrong controller config schema")
	}
	if c.ControllerKey == "" || c.TLSCA == "" || c.WorkstationPublicKey == "" {
		return errors.New("controller key, TLS CA, and workstation public key are required")
	}
	if len(c.RequestedCapabilities) == 0 || len(c.GatewayOperations) == 0 {
		return errors.New("requested capabilities and gateway operations must be non-empty")
	}
	caps := stringSet(c.RequestedCapabilities)
	for _, op := range c.GatewayOperations {
		if _, ok := allowedOperations[op]; !ok {
			return fmt.Errorf("unknown gateway operation: %s", op)
		}
		for _, cap := range operationCapabilities[op] {
			if _, ok := caps[cap]; !ok {
				return fmt.Errorf("%s requires requested capability %s", op, cap)
			}
		}
	}
	if len(c.Endpoints) == 0 {
		return errors.New("endpoints must be non-empty")
	}
	ids := map[string]struct{}{}
	for i := range c.Endpoints {
		ep := &c.Endpoints[i]
		if ep.EndpointID == "" || ep.Host == "" || ep.ServerHostname == "" {
			return errors.New("endpoint id, host, and server_hostname are required")
		}
		if ep.Mode != "DIRECT_STREAM" && ep.Mode != "EDGE_STREAM" {
			if ep.Mode == "DURABLE_RELAY" {
				return errors.New("durable relay is not implemented for controller bootstrap")
			}
			return fmt.Errorf("unsupported endpoint mode: %s", ep.Mode)
		}
		if ep.Port < 1 || ep.Port > 65535 {
			return fmt.Errorf("endpoint %s port outside 1..65535", ep.EndpointID)
		}
		if _, exists := ids[ep.EndpointID]; exists {
			return fmt.Errorf("duplicate endpoint_id: %s", ep.EndpointID)
		}
		ids[ep.EndpointID] = struct{}{}
	}
	if c.MaxPathAgeMS == 0 {
		c.MaxPathAgeMS = 5000
	}
	if c.MaxPathAgeMS < 100 || c.MaxPathAgeMS > 300000 {
		return errors.New("max_path_age_ms outside policy")
	}
	if c.ConnectTimeoutS == 0 {
		c.ConnectTimeoutS = 5
	}
	if c.RequestTimeoutS == 0 {
		c.RequestTimeoutS = 5
	}
	if c.ConnectTimeoutS < .05 || c.ConnectTimeoutS > 60 ||
		c.RequestTimeoutS < .05 || c.RequestTimeoutS > 60 {
		return errors.New("controller timeout outside policy")
	}
	c.RequestedCapabilities = sortedUnique(c.RequestedCapabilities)
	c.GatewayOperations = sortedUnique(c.GatewayOperations)
	sort.SliceStable(c.Endpoints, func(i, j int) bool {
		priority := func(mode string) int {
			if mode == "DIRECT_STREAM" { return 0 }
			return 1
		}
		return priority(c.Endpoints[i].Mode) < priority(c.Endpoints[j].Mode)
	})
	return nil
}

func (c *Config) resolve(value string) string {
	if filepath.IsAbs(value) {
		return filepath.Clean(value)
	}
	return filepath.Clean(filepath.Join(c.baseDir, value))
}

func (c *Config) RequestedSet() map[string]struct{} {
	return stringSet(c.RequestedCapabilities)
}

func (c *Config) OperationSet() map[string]struct{} {
	return stringSet(c.GatewayOperations)
}

func (c *Config) LoadVeraPortCredentials() (*veraport.ClientCredentials, error) {
	controllerPEM, err := os.ReadFile(c.ControllerKey)
	if err != nil { return nil, fmt.Errorf("read controller key: %w", err) }
	controllerKey, err := veraport.ParseP256PrivatePEM(controllerPEM)
	if err != nil { return nil, err }

	workstationPEM, err := os.ReadFile(c.WorkstationPublicKey)
	if err != nil { return nil, fmt.Errorf("read workstation public key: %w", err) }
	workstationKey, err := veraport.ParseP256PublicPEM(workstationPEM)
	if err != nil { return nil, err }

	caPEM, err := os.ReadFile(c.TLSCA)
	if err != nil { return nil, fmt.Errorf("read TLS CA: %w", err) }
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("TLS CA contains no accepted certificates")
	}
	return &veraport.ClientCredentials{
		ControllerPrivateKey: controllerKey,
		WorkstationPublicKey: workstationKey,
		RootCAs: pool,
	}, nil
}

func (c *Config) ConnectTimeout() time.Duration {
	return time.Duration(c.ConnectTimeoutS * float64(time.Second))
}

func (c *Config) RequestTimeout() time.Duration {
	return time.Duration(c.RequestTimeoutS * float64(time.Second))
}

func stringSet(values []string) map[string]struct{} {
	out := make(map[string]struct{}, len(values))
	for _, value := range values {
		out[value] = struct{}{}
	}
	return out
}

func sortedUnique(values []string) []string {
	set := stringSet(values)
	out := make([]string, 0, len(set))
	for value := range set {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

// bytesNewReader is kept tiny so config.go imports stay explicit at call sites.
func bytesNewReader(raw []byte) *bytes.Reader { return bytes.NewReader(raw) }
