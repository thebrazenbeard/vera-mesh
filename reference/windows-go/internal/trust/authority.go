package trust

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
)

var (
	ErrInvalidBinding         = errors.New("invalid trust binding")
	ErrInactive               = errors.New("trust inactive")
	ErrRevoking               = errors.New("trust revoking")
	ErrRevokedIdentity        = errors.New("revoked identity")
	ErrKeyCompromised         = errors.New("key compromised")
	ErrStaleGeneration        = errors.New("stale trust generation")
	ErrBindingMismatch        = errors.New("trust binding mismatch")
	ErrAdmissionBusy          = errors.New("admission decision already in flight")
	ErrTrustClosed            = errors.New("trust authority closed")
	ErrTrustIntegrity         = errors.New("trust authority integrity unknown")
	ErrRevokeMismatch         = errors.New("revoke subject mismatch")
	ErrInvalidRevoke          = errors.New("invalid revoke request")
	ErrDurableDecisionUnknown = errors.New("durable admission decision unknown")
)

type Binding struct {
	AuthenticatedPeerNodeID string `json:"authenticated_peer_node_id"`
	LocalNodeID             string `json:"local_node_id"`
	PairID                  string `json:"pair_id"`
	TrustGeneration         uint64 `json:"trust_generation"`
	CertificateDERSHA256    string `json:"certificate_der_sha256"`
	KeyIdentitySHA256       string `json:"key_identity_sha256"`
}

type AdmissionRequest Binding

type RevokeReason string

const (
	RevokeOrdinary       RevokeReason = "ORDINARY_CERTIFICATE"
	RevokeKeyCompromised RevokeReason = "KEY_COMPROMISED"
)

type RevokeRequest struct {
	PairID               string
	CertificateDERSHA256 string
	KeyIdentitySHA256    string
	Reason               RevokeReason
}

type peerState string

const (
	stateActive   peerState = "ACTIVE"
	stateRevoking peerState = "REVOKING"
	stateRevoked  peerState = "REVOKED"
)

type peerRecord struct {
	Binding Binding
	State   peerState
}

const (
	KeyIdentityAlgorithmID = "ED25519_PKIX_SPKI_DER_SHA256_V1"

	trustJournalMagic      = "VMT1"
	trustJournalHeaderSize = 40
	trustJournalMaxPayload = 1 << 20
)

type persistedRecord struct {
	Type                   string       `json:"type"`
	Binding                Binding      `json:"binding"`
	Reason                 RevokeReason `json:"reason,omitempty"`
	KeyIdentityAlgorithmID string       `json:"key_identity_algorithm_id"`
}

type trustJournalFile interface {
	io.Reader
	io.Writer
	io.Seeker
	Sync() error
	Close() error
}

type Authority struct {
	mu sync.Mutex
	cv *sync.Cond

	path string
	file trustJournalFile

	peers            map[string]peerRecord
	certTombstones   map[string]struct{}
	keyTombstones    map[string]struct{}
	keyRevoking      map[string]struct{}
	inFlightPair     map[string]uint64
	inFlightKey      map[string]uint64
	unresolvedPair   map[string]bool
	unresolvedKey    map[string]bool
	closed           bool
	integrityUnknown bool
}

func Open(path string) (*Authority, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	a := &Authority{
		path:           path,
		file:           f,
		peers:          make(map[string]peerRecord),
		certTombstones: make(map[string]struct{}),
		keyTombstones:  make(map[string]struct{}),
		keyRevoking:    make(map[string]struct{}),
		inFlightPair:   make(map[string]uint64),
		inFlightKey:    make(map[string]uint64),
		unresolvedPair: make(map[string]bool),
		unresolvedKey:  make(map[string]bool),
	}
	a.cv = sync.NewCond(&a.mu)
	if err := a.replay(); err != nil {
		f.Close()
		return nil, err
	}
	if _, err := f.Seek(0, io.SeekEnd); err != nil {
		f.Close()
		return nil, err
	}
	return a, nil
}

func validateBinding(b Binding) error {
	if b.AuthenticatedPeerNodeID == "" || b.LocalNodeID == "" || b.PairID == "" || b.TrustGeneration == 0 || !isLowerSHA256Hex(b.CertificateDERSHA256) || !isLowerSHA256Hex(b.KeyIdentitySHA256) {
		return ErrInvalidBinding
	}
	return nil
}

func isLowerSHA256Hex(s string) bool {
	if len(s) != 64 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') {
			return false
		}
	}
	return true
}

func (a *Authority) Activate(b Binding) error {
	if err := validateBinding(b); err != nil {
		return err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return ErrTrustClosed
	}
	if a.integrityUnknown {
		return ErrTrustIntegrity
	}
	if _, ok := a.certTombstones[b.CertificateDERSHA256]; ok {
		return ErrRevokedIdentity
	}
	if _, ok := a.keyTombstones[b.KeyIdentitySHA256]; ok {
		return ErrKeyCompromised
	}
	if _, ok := a.keyRevoking[b.KeyIdentitySHA256]; ok {
		return ErrRevoking
	}
	if old, ok := a.peers[b.PairID]; ok {
		if old.State == stateRevoking {
			return ErrRevoking
		}
		if old.State == stateActive {
			if old.Binding == b {
				return nil
			}
			return ErrBindingMismatch
		}
		if b.TrustGeneration <= old.Binding.TrustGeneration {
			return ErrStaleGeneration
		}
	}
	if err := a.appendLocked(persistedRecord{Type: "ACTIVE", Binding: b}); err != nil {
		return err
	}
	a.peers[b.PairID] = peerRecord{Binding: b, State: stateActive}
	return nil
}

func (a *Authority) WithDurableAdmission(req AdmissionRequest, commit func(Binding) error) error {
	if commit == nil {
		return ErrInvalidBinding
	}
	b := Binding(req)
	if err := validateBinding(b); err != nil {
		return err
	}
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return ErrTrustClosed
	}
	if a.integrityUnknown {
		a.mu.Unlock()
		return ErrTrustIntegrity
	}
	verified, err := a.verifyAdmissionLocked(b)
	if err != nil {
		a.mu.Unlock()
		return err
	}
	if a.inFlightPair[b.PairID] != 0 {
		a.mu.Unlock()
		return ErrAdmissionBusy
	}
	a.inFlightPair[b.PairID]++
	a.inFlightKey[b.KeyIdentitySHA256]++
	a.mu.Unlock()

	commitErr := commit(verified)

	a.mu.Lock()
	if errors.Is(commitErr, ErrDurableDecisionUnknown) {
		a.unresolvedPair[b.PairID] = true
		a.unresolvedKey[b.KeyIdentitySHA256] = true
	}
	a.inFlightPair[b.PairID]--
	if a.inFlightPair[b.PairID] == 0 {
		delete(a.inFlightPair, b.PairID)
	}
	a.inFlightKey[b.KeyIdentitySHA256]--
	if a.inFlightKey[b.KeyIdentitySHA256] == 0 {
		delete(a.inFlightKey, b.KeyIdentitySHA256)
	}
	a.cv.Broadcast()
	a.mu.Unlock()
	return commitErr
}

func (a *Authority) verifyAdmissionLocked(b Binding) (Binding, error) {
	if _, ok := a.keyTombstones[b.KeyIdentitySHA256]; ok {
		return Binding{}, ErrKeyCompromised
	}
	if _, ok := a.keyRevoking[b.KeyIdentitySHA256]; ok {
		return Binding{}, ErrRevoking
	}
	if _, ok := a.certTombstones[b.CertificateDERSHA256]; ok {
		return Binding{}, ErrRevokedIdentity
	}
	peer, ok := a.peers[b.PairID]
	if !ok {
		return Binding{}, ErrInactive
	}
	switch peer.State {
	case stateRevoking:
		return Binding{}, ErrRevoking
	case stateRevoked:
		return Binding{}, ErrRevokedIdentity
	case stateActive:
	default:
		return Binding{}, ErrTrustIntegrity
	}
	if b.TrustGeneration != peer.Binding.TrustGeneration {
		return Binding{}, ErrStaleGeneration
	}
	if b != peer.Binding {
		return Binding{}, ErrBindingMismatch
	}
	return peer.Binding, nil
}

type Revocation struct {
	a       *Authority
	request RevokeRequest
	binding Binding
	closed  bool
}

func (a *Authority) BeginRevoke(req RevokeRequest) (*Revocation, error) {
	if req.PairID == "" || req.CertificateDERSHA256 == "" || (req.Reason != RevokeOrdinary && req.Reason != RevokeKeyCompromised) {
		return nil, ErrInvalidRevoke
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return nil, ErrTrustClosed
	}
	if a.integrityUnknown {
		return nil, ErrTrustIntegrity
	}
	peer, ok := a.peers[req.PairID]
	if !ok {
		return nil, ErrInactive
	}
	if peer.Binding.CertificateDERSHA256 != req.CertificateDERSHA256 {
		return nil, ErrRevokeMismatch
	}
	if peer.State == stateRevoked {
		if _, tombstoned := a.certTombstones[peer.Binding.CertificateDERSHA256]; !tombstoned {
			return nil, ErrTrustIntegrity
		}
		if req.Reason == RevokeOrdinary {
			return &Revocation{a: a, request: req, binding: peer.Binding}, nil
		}
		if req.KeyIdentitySHA256 == "" {
			req.KeyIdentitySHA256 = peer.Binding.KeyIdentitySHA256
		}
		if req.KeyIdentitySHA256 != peer.Binding.KeyIdentitySHA256 {
			return nil, ErrRevokeMismatch
		}
		if _, tombstoned := a.keyTombstones[peer.Binding.KeyIdentitySHA256]; tombstoned {
			return &Revocation{a: a, request: req, binding: peer.Binding}, nil
		}

		// Monotonic severity also applies after ordinary revocation completed.
		// The retained cert->key binding is the authority for strengthening
		// the local trust domain to KEY_COMPROMISED.
		if err := a.appendLocked(persistedRecord{Type: "REVOKE_REQUESTED", Binding: peer.Binding, Reason: RevokeKeyCompromised}); err != nil {
			return nil, err
		}
		a.keyRevoking[peer.Binding.KeyIdentitySHA256] = struct{}{}
		for id, p := range a.peers {
			if p.State == stateActive && p.Binding.KeyIdentitySHA256 == peer.Binding.KeyIdentitySHA256 {
				p.State = stateRevoking
				a.peers[id] = p
			}
		}
		return &Revocation{a: a, request: req, binding: peer.Binding}, nil
	}
	if peer.State == stateRevoking {
		_, keyScoped := a.keyRevoking[peer.Binding.KeyIdentitySHA256]
		if keyScoped {
			if req.Reason != RevokeKeyCompromised {
				return nil, ErrRevokeMismatch
			}
			if req.KeyIdentitySHA256 == "" {
				req.KeyIdentitySHA256 = peer.Binding.KeyIdentitySHA256
			}
			if req.KeyIdentitySHA256 != peer.Binding.KeyIdentitySHA256 {
				return nil, ErrRevokeMismatch
			}
			return &Revocation{a: a, request: req, binding: peer.Binding}, nil
		}
		if req.Reason == RevokeOrdinary {
			return &Revocation{a: a, request: req, binding: peer.Binding}, nil
		}
		if req.Reason != RevokeKeyCompromised {
			return nil, ErrRevokeMismatch
		}
		if req.KeyIdentitySHA256 == "" {
			req.KeyIdentitySHA256 = peer.Binding.KeyIdentitySHA256
		}
		if req.KeyIdentitySHA256 != peer.Binding.KeyIdentitySHA256 {
			return nil, ErrRevokeMismatch
		}

		// Monotonic severity: an already durable ordinary-cert REVOKING
		// state may be strengthened to a durable key-domain compromise
		// barrier, but never weakened in the other direction.
		if err := a.appendLocked(persistedRecord{Type: "REVOKE_REQUESTED", Binding: peer.Binding, Reason: RevokeKeyCompromised}); err != nil {
			return nil, err
		}
		a.keyRevoking[peer.Binding.KeyIdentitySHA256] = struct{}{}
		for id, p := range a.peers {
			if p.State == stateActive && p.Binding.KeyIdentitySHA256 == peer.Binding.KeyIdentitySHA256 {
				p.State = stateRevoking
				a.peers[id] = p
			}
		}
		return &Revocation{a: a, request: req, binding: peer.Binding}, nil
	}
	if peer.State != stateActive {
		return nil, ErrTrustIntegrity
	}
	if peer.Binding.CertificateDERSHA256 != req.CertificateDERSHA256 {
		return nil, ErrRevokeMismatch
	}
	if req.Reason == RevokeKeyCompromised {
		if req.KeyIdentitySHA256 == "" {
			req.KeyIdentitySHA256 = peer.Binding.KeyIdentitySHA256
		}
		if req.KeyIdentitySHA256 != peer.Binding.KeyIdentitySHA256 {
			return nil, ErrRevokeMismatch
		}
	}

	// The durable non-final witness precedes the protective in-memory barrier.
	if err := a.appendLocked(persistedRecord{Type: "REVOKE_REQUESTED", Binding: peer.Binding, Reason: req.Reason}); err != nil {
		return nil, err
	}
	if req.Reason == RevokeKeyCompromised {
		a.keyRevoking[peer.Binding.KeyIdentitySHA256] = struct{}{}
		for id, p := range a.peers {
			if p.State == stateActive && p.Binding.KeyIdentitySHA256 == peer.Binding.KeyIdentitySHA256 {
				p.State = stateRevoking
				a.peers[id] = p
			}
		}
	} else {
		peer.State = stateRevoking
		a.peers[req.PairID] = peer
	}
	return &Revocation{a: a, request: req, binding: peer.Binding}, nil
}

func (a *Authority) Revoke(req RevokeRequest) error {
	r, err := a.BeginRevoke(req)
	if err != nil {
		return err
	}
	return r.Complete()
}

func (r *Revocation) Complete() error {
	if r == nil || r.a == nil {
		return ErrInvalidRevoke
	}
	a := r.a
	a.mu.Lock()
	defer a.mu.Unlock()
	if r.closed {
		return ErrRevokedIdentity
	}
	for {
		var n uint64
		if r.request.Reason == RevokeKeyCompromised {
			n = a.inFlightKey[r.binding.KeyIdentitySHA256]
		} else {
			n = a.inFlightPair[r.binding.PairID]
		}
		if n == 0 {
			break
		}
		a.cv.Wait()
	}
	if a.closed {
		return ErrTrustClosed
	}
	if a.integrityUnknown {
		return ErrTrustIntegrity
	}
	if r.request.Reason == RevokeKeyCompromised {
		if _, done := a.keyTombstones[r.binding.KeyIdentitySHA256]; done {
			r.closed = true
			return nil
		}
	} else if _, done := a.certTombstones[r.binding.CertificateDERSHA256]; done {
		r.closed = true
		return nil
	}
	if r.request.Reason == RevokeKeyCompromised {
		if a.unresolvedKey[r.binding.KeyIdentitySHA256] {
			return ErrDurableDecisionUnknown
		}
	} else if a.unresolvedPair[r.binding.PairID] {
		return ErrDurableDecisionUnknown
	}
	if err := a.appendLocked(persistedRecord{Type: "REVOKED", Binding: r.binding, Reason: r.request.Reason}); err != nil {
		return err
	}
	a.certTombstones[r.binding.CertificateDERSHA256] = struct{}{}
	if r.request.Reason == RevokeKeyCompromised {
		a.keyTombstones[r.binding.KeyIdentitySHA256] = struct{}{}
		delete(a.keyRevoking, r.binding.KeyIdentitySHA256)
		for id, p := range a.peers {
			if p.Binding.KeyIdentitySHA256 == r.binding.KeyIdentitySHA256 {
				p.State = stateRevoked
				a.peers[id] = p
			}
		}
	} else if peer, ok := a.peers[r.binding.PairID]; ok {
		peer.State = stateRevoked
		a.peers[r.binding.PairID] = peer
	}
	r.closed = true
	return nil
}

func (a *Authority) Close() error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return nil
	}
	a.closed = true
	a.cv.Broadcast()
	if a.file == nil {
		return nil
	}
	err := a.file.Close()
	a.file = nil
	return err
}

func (a *Authority) appendLocked(rec persistedRecord) error {
	rec.KeyIdentityAlgorithmID = KeyIdentityAlgorithmID
	payload, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if len(payload) == 0 || len(payload) > trustJournalMaxPayload {
		return ErrTrustIntegrity
	}
	var header [trustJournalHeaderSize]byte
	copy(header[:4], trustJournalMagic)
	binary.BigEndian.PutUint32(header[4:8], uint32(len(payload)))
	sum := sha256.Sum256(payload)
	copy(header[8:], sum[:])
	frame := make([]byte, 0, len(header)+len(payload))
	frame = append(frame, header[:]...)
	frame = append(frame, payload...)

	start, err := a.file.Seek(0, io.SeekCurrent)
	if err != nil {
		return a.markIntegrityUnknown("locate append boundary", err)
	}
	n, err := a.file.Write(frame)
	if err != nil {
		return a.markIntegrityUnknown("write trust frame", err)
	}
	if n != len(frame) {
		return a.markIntegrityUnknown("write trust frame", io.ErrShortWrite)
	}
	if err := a.file.Sync(); err != nil {
		return a.markIntegrityUnknown("sync trust frame", err)
	}
	if _, err := a.file.Seek(start, io.SeekStart); err != nil {
		return a.markIntegrityUnknown("seek persisted frame", err)
	}
	readback := make([]byte, len(frame))
	if _, err := io.ReadFull(a.file, readback); err != nil {
		return a.markIntegrityUnknown("read persisted frame", err)
	}
	if !equal(frame, readback) {
		return a.markIntegrityUnknown("verify persisted frame", errors.New("persisted bytes differ"))
	}
	return nil
}

func (a *Authority) markIntegrityUnknown(stage string, cause error) error {
	a.integrityUnknown = true
	return fmt.Errorf("%w: %s: %v", ErrTrustIntegrity, stage, cause)
}

func (a *Authority) replay() error {
	if _, err := a.file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	for {
		var header [trustJournalHeaderSize]byte
		n, err := io.ReadFull(a.file, header[:])
		if err == io.EOF && n == 0 {
			return nil
		}
		if err != nil {
			return fmt.Errorf("%w: incomplete trust journal header", ErrTrustIntegrity)
		}
		if string(header[:4]) != trustJournalMagic {
			return ErrTrustIntegrity
		}
		sz := binary.BigEndian.Uint32(header[4:8])
		if sz == 0 || sz > trustJournalMaxPayload {
			return ErrTrustIntegrity
		}
		payload := make([]byte, sz)
		if _, err := io.ReadFull(a.file, payload); err != nil {
			return fmt.Errorf("%w: incomplete trust journal payload", ErrTrustIntegrity)
		}
		got := sha256.Sum256(payload)
		if !equal(header[8:], got[:]) {
			return ErrTrustIntegrity
		}
		var rec persistedRecord
		if err := json.Unmarshal(payload, &rec); err != nil || validateBinding(rec.Binding) != nil || rec.KeyIdentityAlgorithmID != KeyIdentityAlgorithmID {
			return ErrTrustIntegrity
		}
		switch rec.Type {
		case "ACTIVE":
			if _, tomb := a.certTombstones[rec.Binding.CertificateDERSHA256]; tomb {
				return ErrTrustIntegrity
			}
			if _, tomb := a.keyTombstones[rec.Binding.KeyIdentitySHA256]; tomb {
				return ErrTrustIntegrity
			}
			if old, ok := a.peers[rec.Binding.PairID]; ok && rec.Binding.TrustGeneration <= old.Binding.TrustGeneration && old.Binding != rec.Binding {
				return ErrTrustIntegrity
			}
			a.peers[rec.Binding.PairID] = peerRecord{Binding: rec.Binding, State: stateActive}
		case "REVOKE_REQUESTED":
			peer, ok := a.peers[rec.Binding.PairID]
			if !ok || peer.Binding != rec.Binding {
				return ErrTrustIntegrity
			}
			if rec.Reason == RevokeKeyCompromised {
				a.keyRevoking[rec.Binding.KeyIdentitySHA256] = struct{}{}
				for id, p := range a.peers {
					if p.State == stateActive && p.Binding.KeyIdentitySHA256 == rec.Binding.KeyIdentitySHA256 {
						p.State = stateRevoking
						a.peers[id] = p
					}
				}
			} else if rec.Reason == RevokeOrdinary {
				peer.State = stateRevoking
				a.peers[rec.Binding.PairID] = peer
			} else {
				return ErrTrustIntegrity
			}
		case "REVOKED":
			a.certTombstones[rec.Binding.CertificateDERSHA256] = struct{}{}
			if rec.Reason == RevokeKeyCompromised {
				a.keyTombstones[rec.Binding.KeyIdentitySHA256] = struct{}{}
				delete(a.keyRevoking, rec.Binding.KeyIdentitySHA256)
				for id, p := range a.peers {
					if p.Binding.KeyIdentitySHA256 == rec.Binding.KeyIdentitySHA256 {
						p.State = stateRevoked
						a.peers[id] = p
					}
				}
			} else if rec.Reason == RevokeOrdinary {
				peer, ok := a.peers[rec.Binding.PairID]
				if !ok || peer.Binding != rec.Binding {
					return ErrTrustIntegrity
				}
				peer.State = stateRevoked
				a.peers[rec.Binding.PairID] = peer
			} else {
				return ErrTrustIntegrity
			}
		default:
			return ErrTrustIntegrity
		}
	}
}

func equal(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	var d byte
	for i := range a {
		d |= a[i] ^ b[i]
	}
	return d == 0
}
