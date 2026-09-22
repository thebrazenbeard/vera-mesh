package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/auth"
)

type IntrospectionConfig struct {
	Schema            string  `json:"schema"`
	Endpoint          string  `json:"introspection_endpoint"`
	ClientID          string  `json:"client_id"`
	ClientSecretEnv   string  `json:"client_secret_env"`
	ExpectedIssuer    string  `json:"expected_issuer"`
	ExpectedResource  string  `json:"expected_resource"`
	ClientAuthMethod  string  `json:"client_auth_method"`
	TimeoutS          float64 `json:"timeout_s"`
}

func (c *IntrospectionConfig) Validate() error {
	if c.Schema != "VERAMESH_OAUTH_INTROSPECTION_V1" {
		return errors.New("wrong introspection config schema")
	}
	for name, value := range map[string]string{
		"introspection_endpoint": c.Endpoint,
		"expected_issuer":        c.ExpectedIssuer,
		"expected_resource":      c.ExpectedResource,
	} {
		parsed, err := url.Parse(value)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" ||
			parsed.User != nil || parsed.Fragment != "" {
			return fmt.Errorf("%s must be an absolute HTTPS URL without userinfo/fragment", name)
		}
	}
	if strings.TrimSpace(c.ClientID) == "" {
		return errors.New("client_id is required")
	}
	if strings.TrimSpace(c.ClientSecretEnv) == "" {
		return errors.New("client_secret_env is required")
	}
	for _, r := range c.ClientSecretEnv {
		if !(r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9') {
			return errors.New("client_secret_env must be an environment-variable name")
		}
	}
	if c.ClientAuthMethod == "" {
		c.ClientAuthMethod = "client_secret_basic"
	}
	if c.ClientAuthMethod != "client_secret_basic" &&
		c.ClientAuthMethod != "client_secret_post" {
		return errors.New("client_auth_method must be client_secret_basic or client_secret_post")
	}
	if c.TimeoutS == 0 {
		c.TimeoutS = 5
	}
	if c.TimeoutS < 0.1 || c.TimeoutS > 30 {
		return errors.New("timeout_s outside 0.1..30")
	}
	c.ExpectedIssuer = strings.TrimRight(c.ExpectedIssuer, "/")
	c.ExpectedResource = strings.TrimRight(c.ExpectedResource, "/")
	return nil
}

type IntrospectionVerifier struct {
	cfg    IntrospectionConfig
	client *http.Client
	now    func() time.Time
}

func NewIntrospectionVerifier(cfg IntrospectionConfig) (*IntrospectionVerifier, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	client := &http.Client{
		Timeout: time.Duration(cfg.TimeoutS * float64(time.Second)),
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return &IntrospectionVerifier{
		cfg: cfg,
		client: client,
		now: time.Now,
	}, nil
}

func (v *IntrospectionVerifier) Verify(
	ctx context.Context,
	token string,
	_ *http.Request,
) (*auth.TokenInfo, error) {
	if token == "" {
		return nil, auth.ErrInvalidToken
	}
	secret := os.Getenv(v.cfg.ClientSecretEnv)
	if secret == "" {
		return nil, fmt.Errorf("%w: introspection client secret unavailable", auth.ErrInvalidToken)
	}

	form := url.Values{}
	form.Set("token", token)
	form.Set("token_type_hint", "access_token")
	if v.cfg.ClientAuthMethod == "client_secret_post" {
		form.Set("client_id", v.cfg.ClientID)
		form.Set("client_secret", secret)
	}
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		v.cfg.Endpoint,
		strings.NewReader(form.Encode()),
	)
	if err != nil {
		return nil, fmt.Errorf("%w: introspection request construction failed", auth.ErrInvalidToken)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	if v.cfg.ClientAuthMethod == "client_secret_basic" {
		req.SetBasicAuth(v.cfg.ClientID, secret)
	}

	resp, err := v.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: introspection request failed", auth.ErrInvalidToken)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("%w: introspection returned HTTP %d", auth.ErrInvalidToken, resp.StatusCode)
	}
	limited := io.LimitReader(resp.Body, 262145)
	raw, err := io.ReadAll(limited)
	if err != nil || len(raw) > 262144 {
		return nil, fmt.Errorf("%w: invalid introspection response size", auth.ErrInvalidToken)
	}
	var claims map[string]any
	if err := json.Unmarshal(raw, &claims); err != nil {
		return nil, fmt.Errorf("%w: invalid introspection JSON", auth.ErrInvalidToken)
	}
	active, ok := claims["active"].(bool)
	if !ok || !active {
		return nil, auth.ErrInvalidToken
	}
	issuer, ok := claims["iss"].(string)
	if !ok || strings.TrimRight(issuer, "/") != v.cfg.ExpectedIssuer {
		return nil, fmt.Errorf("%w: issuer mismatch", auth.ErrInvalidToken)
	}
	subject, ok := claims["sub"].(string)
	if !ok || strings.TrimSpace(subject) == "" {
		return nil, fmt.Errorf("%w: subject missing", auth.ErrInvalidToken)
	}
	if !resourceMatches(claims, v.cfg.ExpectedResource) {
		return nil, fmt.Errorf("%w: resource/audience mismatch", auth.ErrInvalidToken)
	}
	exp, ok := exactUnix(claims["exp"])
	if !ok || !time.Unix(exp, 0).After(v.now()) {
		return nil, fmt.Errorf("%w: token expired or exp missing", auth.ErrInvalidToken)
	}
	scopes, ok := tokenScopes(claims["scope"])
	if !ok {
		return nil, fmt.Errorf("%w: scope missing or invalid", auth.ErrInvalidToken)
	}
	return &auth.TokenInfo{
		Scopes:     scopes,
		Expiration: time.Unix(exp, 0),
		UserID:     subject,
		Extra:      claims,
	}, nil
}

func resourceMatches(claims map[string]any, expected string) bool {
	if resource, ok := claims["resource"].(string); ok && strings.TrimRight(resource, "/") == expected {
		return true
	}
	switch aud := claims["aud"].(type) {
	case string:
		return strings.TrimRight(aud, "/") == expected
	case []any:
		for _, item := range aud {
			value, ok := item.(string)
			if ok && strings.TrimRight(value, "/") == expected {
				return true
			}
		}
	}
	return false
}

func exactUnix(value any) (int64, bool) {
	switch v := value.(type) {
	case float64:
		i := int64(v)
		return i, float64(i) == v
	case int64:
		return v, true
	case json.Number:
		i, err := v.Int64()
		return i, err == nil
	default:
		return 0, false
	}
}

func tokenScopes(value any) ([]string, bool) {
	switch v := value.(type) {
	case string:
		return strings.Fields(v), true
	case []any:
		out := make([]string, 0, len(v))
		seen := map[string]struct{}{}
		for _, raw := range v {
			item, ok := raw.(string)
			if !ok || item == "" {
				return nil, false
			}
			if _, exists := seen[item]; !exists {
				seen[item] = struct{}{}
				out = append(out, item)
			}
		}
		return out, true
	default:
		return nil, false
	}
}
