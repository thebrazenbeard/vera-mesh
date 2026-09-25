package veraport

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"
)

const (
	ProtocolVersion = "veraport-v1"
	ALPN            = "veraport/1"

	domainClient = "veramesh-veraport-v1/session-client-auth\n"
	domainServer = "veramesh-veraport-v1/session-server-accept\n"
)

var rawURL = base64.RawURLEncoding

type ServerChallenge struct {
	FrameType            string `json:"frame_type"`
	ProtocolVersion      string `json:"protocol_version"`
	WorkstationPrincipal string `json:"workstation_principal"`
	WorkstationKeyID     string `json:"workstation_key_id"`
	Challenge            string `json:"challenge"`
}

type ClientAuth struct {
	FrameType             string   `json:"frame_type"`
	ProtocolVersion       string   `json:"protocol_version"`
	ControllerPrincipal   string   `json:"controller_principal"`
	ControllerKeyID       string   `json:"controller_key_id"`
	WorkstationPrincipal  string   `json:"workstation_principal"`
	ServerChallenge       string   `json:"server_challenge"`
	ClientNonce           string   `json:"client_nonce"`
	RequestedCapabilities []string `json:"requested_capabilities"`
	Signature             string   `json:"signature"`
}

type ServerAccept struct {
	FrameType            string   `json:"frame_type"`
	ProtocolVersion      string   `json:"protocol_version"`
	SessionID            string   `json:"session_id"`
	ControllerPrincipal  string   `json:"controller_principal"`
	WorkstationPrincipal string   `json:"workstation_principal"`
	WorkstationKeyID     string   `json:"workstation_key_id"`
	ServerChallenge      string   `json:"server_challenge"`
	ClientNonce          string   `json:"client_nonce"`
	GrantedCapabilities  []string `json:"granted_capabilities"`
	ExpiresAtMS          int64    `json:"expires_at_ms"`
	Signature            string   `json:"signature"`
	Code                 string   `json:"code,omitempty"`
	Message              string   `json:"message,omitempty"`
}

type SessionBinding struct {
	SessionID            string
	ControllerPrincipal  string
	WorkstationPrincipal string
	GrantedCapabilities  map[string]struct{}
	ExpiresAtMS          int64
}

func ParseP256PrivatePEM(data []byte) (*ecdsa.PrivateKey, error) {
	block, rest := pem.Decode(data)
	if block == nil || len(rest) != 0 {
		return nil, errors.New("controller key must contain exactly one PEM block")
	}
	key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse PKCS8 controller key: %w", err)
	}
	ec, ok := key.(*ecdsa.PrivateKey)
	if !ok || ec.Curve != elliptic.P256() {
		return nil, errors.New("controller key must be EC P-256 PKCS8")
	}
	return ec, nil
}

func ParseP256PublicPEM(data []byte) (*ecdsa.PublicKey, error) {
	block, rest := pem.Decode(data)
	if block == nil || len(rest) != 0 {
		return nil, errors.New("workstation key must contain exactly one PEM block")
	}
	key, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse workstation public key: %w", err)
	}
	ec, ok := key.(*ecdsa.PublicKey)
	if !ok || ec.Curve != elliptic.P256() {
		return nil, errors.New("workstation key must be EC P-256 SubjectPublicKeyInfo")
	}
	return ec, nil
}

func PrincipalID(publicKey *ecdsa.PublicKey, prefix string) (string, error) {
	id, err := KeyID(publicKey)
	if err != nil {
		return "", err
	}
	if prefix == "" {
		return "", errors.New("principal prefix is required")
	}
	return prefix + ":" + id, nil
}

func KeyID(publicKey *ecdsa.PublicKey) (string, error) {
	if publicKey == nil || publicKey.Curve != elliptic.P256() {
		return "", errors.New("VeraPort V1 requires an EC P-256 public key")
	}
	spki, err := x509.MarshalPKIXPublicKey(publicKey)
	if err != nil {
		return "", fmt.Errorf("marshal public key: %w", err)
	}
	sum := sha256.Sum256(spki)
	return hex.EncodeToString(sum[:]), nil
}

func field(name, value string) string {
	return name + "=" + strconv.Itoa(len([]byte(value))) + ":" + value + "\n"
}

func normalizeCapabilities(values []string) []string {
	set := map[string]struct{}{}
	for _, value := range values {
		set[value] = struct{}{}
	}
	out := make([]string, 0, len(set))
	for value := range set {
		out = append(out, value)
	}
	sort.Strings(out)
	return out
}

func capabilityString(values []string) string {
	return strings.Join(normalizeCapabilities(values), ",")
}

func (x *ClientAuth) SignatureBase() []byte {
	return []byte(domainClient +
		field("protocol_version", x.ProtocolVersion) +
		field("controller_principal", x.ControllerPrincipal) +
		field("controller_key_id", x.ControllerKeyID) +
		field("workstation_principal", x.WorkstationPrincipal) +
		field("server_challenge", x.ServerChallenge) +
		field("client_nonce", x.ClientNonce) +
		field("requested_capabilities", capabilityString(x.RequestedCapabilities)))
}

func (x *ServerAccept) SignatureBase() []byte {
	return []byte(domainServer +
		field("protocol_version", x.ProtocolVersion) +
		field("session_id", x.SessionID) +
		field("controller_principal", x.ControllerPrincipal) +
		field("workstation_principal", x.WorkstationPrincipal) +
		field("workstation_key_id", x.WorkstationKeyID) +
		field("server_challenge", x.ServerChallenge) +
		field("client_nonce", x.ClientNonce) +
		field("granted_capabilities", capabilityString(x.GrantedCapabilities)) +
		field("expires_at_ms", strconv.FormatInt(x.ExpiresAtMS, 10)))
}

func signP1363(privateKey *ecdsa.PrivateKey, payload []byte) (string, error) {
	if privateKey == nil || privateKey.Curve != elliptic.P256() {
		return "", errors.New("VeraPort V1 requires an EC P-256 private key")
	}
	digest := sha256.Sum256(payload)
	r, s, err := ecdsa.Sign(rand.Reader, privateKey, digest[:])
	if err != nil {
		return "", fmt.Errorf("sign P-256 payload: %w", err)
	}
	raw := make([]byte, 64)
	r.FillBytes(raw[:32])
	s.FillBytes(raw[32:])
	return rawURL.EncodeToString(raw), nil
}

func verifyP1363(publicKey *ecdsa.PublicKey, signature string, payload []byte) error {
	if publicKey == nil || publicKey.Curve != elliptic.P256() {
		return errors.New("VeraPort V1 requires an EC P-256 public key")
	}
	raw, err := rawURL.DecodeString(signature)
	if err != nil || len(raw) != 64 {
		return errors.New("invalid P-256 P1363 signature")
	}
	r := new(big.Int).SetBytes(raw[:32])
	s := new(big.Int).SetBytes(raw[32:])
	digest := sha256.Sum256(payload)
	if !ecdsa.Verify(publicKey, digest[:], r, s) {
		return errors.New("P-256 signature verification failed")
	}
	return nil
}

func CreateClientAuth(
	controllerPrivateKey *ecdsa.PrivateKey,
	challenge ServerChallenge,
	requestedCapabilities []string,
	nonce []byte,
) (*ClientAuth, error) {
	if challenge.FrameType != "server_challenge" ||
		challenge.ProtocolVersion != ProtocolVersion {
		return nil, errors.New("expected VeraPort V1 server_challenge")
	}
	challengeBytes, err := rawURL.DecodeString(challenge.Challenge)
	if err != nil || len(challengeBytes) != 32 {
		return nil, errors.New("server challenge must be exactly 32 bytes")
	}
	if nonce == nil {
		nonce = make([]byte, 32)
		if _, err := rand.Read(nonce); err != nil {
			return nil, fmt.Errorf("generate client nonce: %w", err)
		}
	}
	if len(nonce) != 32 {
		return nil, errors.New("client nonce must be exactly 32 bytes")
	}
	controllerPrincipal, err := PrincipalID(&controllerPrivateKey.PublicKey, "controller")
	if err != nil {
		return nil, err
	}
	controllerKeyID, err := KeyID(&controllerPrivateKey.PublicKey)
	if err != nil {
		return nil, err
	}
	auth := &ClientAuth{
		FrameType:             "client_auth",
		ProtocolVersion:       ProtocolVersion,
		ControllerPrincipal:   controllerPrincipal,
		ControllerKeyID:       controllerKeyID,
		WorkstationPrincipal:  challenge.WorkstationPrincipal,
		ServerChallenge:       challenge.Challenge,
		ClientNonce:           rawURL.EncodeToString(nonce),
		RequestedCapabilities: normalizeCapabilities(requestedCapabilities),
	}
	auth.Signature, err = signP1363(controllerPrivateKey, auth.SignatureBase())
	if err != nil {
		return nil, err
	}
	return auth, nil
}

func VerifyServerAccept(
	challenge ServerChallenge,
	client *ClientAuth,
	accept ServerAccept,
	workstationPublicKey *ecdsa.PublicKey,
	nowMS int64,
) (*SessionBinding, error) {
	if client == nil {
		return nil, errors.New("client auth is required")
	}
	if accept.FrameType != "server_accept" {
		if accept.FrameType == "session_reject" {
			return nil, fmt.Errorf("session rejected: %s: %s", accept.Code, accept.Message)
		}
		return nil, errors.New("expected server_accept")
	}
	if challenge.ProtocolVersion != ProtocolVersion ||
		client.ProtocolVersion != ProtocolVersion ||
		accept.ProtocolVersion != ProtocolVersion {
		return nil, errors.New("protocol_version must be veraport-v1")
	}
	expectedPrincipal, err := PrincipalID(workstationPublicKey, "workstation")
	if err != nil {
		return nil, err
	}
	expectedKeyID, err := KeyID(workstationPublicKey)
	if err != nil {
		return nil, err
	}
	if challenge.WorkstationPrincipal != expectedPrincipal ||
		challenge.WorkstationKeyID != expectedKeyID ||
		accept.WorkstationPrincipal != expectedPrincipal ||
		accept.WorkstationKeyID != expectedKeyID {
		return nil, errors.New("workstation identity does not match pinned public key")
	}
	if accept.ControllerPrincipal != client.ControllerPrincipal {
		return nil, errors.New("server accept bound a different controller")
	}
	if accept.ServerChallenge != challenge.Challenge ||
		accept.ClientNonce != client.ClientNonce {
		return nil, errors.New("server accept does not bind both session nonces")
	}
	requested := map[string]struct{}{}
	for _, cap := range client.RequestedCapabilities {
		requested[cap] = struct{}{}
	}
	granted := map[string]struct{}{}
	for _, cap := range accept.GrantedCapabilities {
		if _, ok := requested[cap]; !ok {
			return nil, fmt.Errorf("server granted unrequested capability: %s", cap)
		}
		granted[cap] = struct{}{}
	}
	if nowMS >= accept.ExpiresAtMS {
		return nil, errors.New("server accept is expired")
	}
	if err := verifyP1363(
		workstationPublicKey,
		accept.Signature,
		accept.SignatureBase(),
	); err != nil {
		return nil, err
	}
	return &SessionBinding{
		SessionID:            accept.SessionID,
		ControllerPrincipal:  accept.ControllerPrincipal,
		WorkstationPrincipal: accept.WorkstationPrincipal,
		GrantedCapabilities:  granted,
		ExpiresAtMS:          accept.ExpiresAtMS,
	}, nil
}
