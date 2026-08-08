package inbox

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
)

const DurableInboxAccepted = "DURABLE_INBOX_ACCEPTED"

const (
	journalMagic                     = "VMJ1"
	journalFrameVersion         byte = 1
	journalPayloadLengthOffset       = 8
	journalPayloadDigestOffset       = 12
	journalHeaderDigestOffset        = 44
	journalHeaderSize                = 76
	journalMaxRecordPayloadSize      = 16 << 20 // local journal allocation guard, not a Mesh NOTE/product limit
)

var (
	ErrTrustInactive                 = errors.New("trust inactive")
	ErrPairMismatch                  = errors.New("pair mismatch")
	ErrTrustGenerationMismatch       = errors.New("trust generation mismatch")
	ErrPayloadDigestMismatch         = errors.New("payload digest mismatch")
	ErrMessageIDConflict             = errors.New("message id conflict")
	ErrJournalCorrupt                = errors.New("journal corrupt")
	ErrMalformedEnvelope             = errors.New("malformed envelope")
	ErrStoreNeedsRecovery            = errors.New("store needs recovery")
	ErrStoreClosed                   = errors.New("store closed")
	ErrJournalFrameTooLarge          = errors.New("journal frame too large")
	ErrTrustedNodeContextMissing     = errors.New("trusted node context missing")
	ErrAuthenticatedPeerNodeMismatch = errors.New("authenticated peer node mismatch")
	ErrLocalRecipientNodeMismatch    = errors.New("local recipient node mismatch")
)

type Envelope struct {
	MessageID        string `json:"message_id"`
	PairID           string `json:"pair_id"`
	TrustGeneration  uint64 `json:"trust_generation"`
	SenderNodeID     string `json:"sender_node_id"`
	RecipientNodeID  string `json:"recipient_node_id"`
	MessageSchemaID  string `json:"message_schema_id"`
	Payload          []byte `json:"payload"`
	PayloadByteCount uint64 `json:"payload_byte_count"`
	PayloadSHA256    string `json:"payload_sha256"`
}

// TrustContext is admission evidence supplied by the authenticated transport/local
// trust layer. AuthenticatedPeerNodeID and LocalNodeID must be derived independently
// of the Envelope being validated. A detached TrustContext is not, by itself, final
// revocation serialization: the future authoritative trust layer must hold an admission
// guard across Accept through the durable Sync decision so revoke and accept have a
// mechanically total order.
type TrustContext struct {
	Active                  bool
	PairID                  string
	TrustGeneration         uint64
	AuthenticatedPeerNodeID string
	LocalNodeID             string
}

type Receipt struct {
	MessageID   string `json:"message_id"`
	Disposition string `json:"disposition"`
}

type StoredMessage struct {
	Envelope    Envelope `json:"envelope"`
	Disposition string   `json:"disposition"`
}

type journalRecord struct {
	Version     uint8    `json:"version"`
	Envelope    Envelope `json:"envelope"`
	Disposition string   `json:"disposition"`
}

type journalFile interface {
	io.Reader
	io.Writer
	io.Seeker
	Sync() error
	Truncate(size int64) error
	Close() error
}

type RecoveryState struct {
	TrailingPartialDiscardedBytes int64
}

type Store struct {
	mu       sync.Mutex
	path     string
	file     journalFile
	byID     map[string]StoredMessage
	poisoned bool
	recovery RecoveryState
}

func Open(path string) (*Store, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	s := &Store{path: path, file: f, byID: make(map[string]StoredMessage)}
	if err := s.replay(); err != nil {
		f.Close()
		return nil, err
	}
	if _, err := f.Seek(0, io.SeekEnd); err != nil {
		f.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Accept(trust TrustContext, env Envelope) (Receipt, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.file == nil {
		return Receipt{}, ErrStoreClosed
	}
	if s.poisoned {
		return Receipt{}, ErrStoreNeedsRecovery
	}
	if err := validateEnvelope(trust, env); err != nil {
		return Receipt{}, err
	}
	if prior, ok := s.byID[env.MessageID]; ok {
		if !sameEnvelope(prior.Envelope, env) {
			return Receipt{}, ErrMessageIDConflict
		}
		return Receipt{MessageID: env.MessageID, Disposition: prior.Disposition}, nil
	}
	rec := journalRecord{Version: 1, Envelope: cloneEnvelope(env), Disposition: DurableInboxAccepted}
	if err := s.append(rec); err != nil {
		if !errors.Is(err, ErrJournalFrameTooLarge) {
			s.poisoned = true
		}
		return Receipt{}, err
	}
	s.byID[env.MessageID] = StoredMessage{Envelope: cloneEnvelope(env), Disposition: DurableInboxAccepted}
	return Receipt{MessageID: env.MessageID, Disposition: DurableInboxAccepted}, nil
}

func (s *Store) Count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.byID)
}

func (s *Store) Recovery() RecoveryState {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.recovery
}

func (s *Store) Get(messageID string) (StoredMessage, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	got, ok := s.byID[messageID]
	if !ok {
		return StoredMessage{}, false
	}
	got.Envelope = cloneEnvelope(got.Envelope)
	return got, true
}

func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.file == nil {
		return nil
	}
	err := s.file.Close()
	s.file = nil
	return err
}

func validateEnvelope(trust TrustContext, env Envelope) error {
	if !trust.Active {
		return ErrTrustInactive
	}
	if trust.PairID != env.PairID {
		return ErrPairMismatch
	}
	if trust.TrustGeneration != env.TrustGeneration {
		return ErrTrustGenerationMismatch
	}
	if err := validateDurableBinding(env); err != nil {
		return err
	}
	if trust.AuthenticatedPeerNodeID == "" || trust.LocalNodeID == "" {
		return ErrTrustedNodeContextMissing
	}
	if env.SenderNodeID != trust.AuthenticatedPeerNodeID {
		return ErrAuthenticatedPeerNodeMismatch
	}
	if env.RecipientNodeID != trust.LocalNodeID {
		return ErrLocalRecipientNodeMismatch
	}
	return nil
}

func validateDurableBinding(env Envelope) error {
	if err := ValidateMessageID(env.MessageID); err != nil {
		return err
	}
	if env.PairID == "" || env.SenderNodeID == "" || env.RecipientNodeID == "" || env.MessageSchemaID == "" {
		return fmt.Errorf("%w: required durable binding field missing", ErrMalformedEnvelope)
	}
	if uint64(len(env.Payload)) != env.PayloadByteCount {
		return ErrPayloadDigestMismatch
	}
	sum := sha256.Sum256(env.Payload)
	if env.PayloadSHA256 != hex.EncodeToString(sum[:]) {
		return ErrPayloadDigestMismatch
	}
	return nil
}

func sameEnvelope(a, b Envelope) bool {
	if a.MessageID != b.MessageID || a.PairID != b.PairID || a.TrustGeneration != b.TrustGeneration ||
		a.SenderNodeID != b.SenderNodeID || a.RecipientNodeID != b.RecipientNodeID || a.MessageSchemaID != b.MessageSchemaID ||
		a.PayloadByteCount != b.PayloadByteCount || a.PayloadSHA256 != b.PayloadSHA256 || len(a.Payload) != len(b.Payload) {
		return false
	}
	for i := range a.Payload {
		if a.Payload[i] != b.Payload[i] {
			return false
		}
	}
	return true
}

func cloneEnvelope(in Envelope) Envelope {
	out := in
	out.Payload = append([]byte(nil), in.Payload...)
	return out
}

func (s *Store) append(rec journalRecord) error {
	payload, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if len(payload) > journalMaxRecordPayloadSize {
		return ErrJournalFrameTooLarge
	}
	header := makeJournalHeader(payload)
	if _, err := s.file.Write(header[:]); err != nil {
		return err
	}
	if _, err := s.file.Write(payload); err != nil {
		return err
	}
	return s.file.Sync()
}

func makeJournalHeader(payload []byte) [journalHeaderSize]byte {
	var header [journalHeaderSize]byte
	copy(header[:4], journalMagic)
	header[4] = journalFrameVersion
	binary.BigEndian.PutUint32(header[journalPayloadLengthOffset:journalPayloadLengthOffset+4], uint32(len(payload)))
	payloadSum := sha256.Sum256(payload)
	copy(header[journalPayloadDigestOffset:journalHeaderDigestOffset], payloadSum[:])
	headerSum := sha256.Sum256(header[:journalHeaderDigestOffset])
	copy(header[journalHeaderDigestOffset:], headerSum[:])
	return header
}

func validJournalHeader(header []byte) bool {
	if len(header) != journalHeaderSize || string(header[:4]) != journalMagic || header[4] != journalFrameVersion {
		return false
	}
	if header[5] != 0 || header[6] != 0 || header[7] != 0 {
		return false
	}
	want := sha256.Sum256(header[:journalHeaderDigestOffset])
	return equalBytes(header[journalHeaderDigestOffset:], want[:])
}

func (s *Store) replay() error {
	if _, err := s.file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	for {
		frameStart, err := s.file.Seek(0, io.SeekCurrent)
		if err != nil {
			return err
		}
		var header [journalHeaderSize]byte
		n, err := io.ReadFull(s.file, header[:])
		if err == io.EOF && n == 0 {
			return nil
		}
		if err == io.ErrUnexpectedEOF || (err == io.EOF && n != 0) {
			return s.recoverTrailingPartial(frameStart)
		}
		if err != nil {
			return err
		}
		if !validJournalHeader(header[:]) {
			return ErrJournalCorrupt
		}
		size := binary.BigEndian.Uint32(header[journalPayloadLengthOffset : journalPayloadLengthOffset+4])
		if size > journalMaxRecordPayloadSize {
			return ErrJournalCorrupt
		}
		payload := make([]byte, size)
		if _, err := io.ReadFull(s.file, payload); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return s.recoverTrailingPartial(frameStart)
			}
			return err
		}
		want := header[journalPayloadDigestOffset:journalHeaderDigestOffset]
		got := sha256.Sum256(payload)
		if !equalBytes(want, got[:]) {
			return ErrJournalCorrupt
		}
		var rec journalRecord
		if err := json.Unmarshal(payload, &rec); err != nil || rec.Version != 1 || rec.Disposition != DurableInboxAccepted {
			return ErrJournalCorrupt
		}
		if err := validateDurableBinding(rec.Envelope); err != nil {
			return fmt.Errorf("%w: invalid durable binding: %v", ErrJournalCorrupt, err)
		}
		if prior, ok := s.byID[rec.Envelope.MessageID]; ok {
			if !sameEnvelope(prior.Envelope, rec.Envelope) || prior.Disposition != rec.Disposition {
				return ErrJournalCorrupt
			}
			return ErrJournalCorrupt
		}
		s.byID[rec.Envelope.MessageID] = StoredMessage{Envelope: cloneEnvelope(rec.Envelope), Disposition: rec.Disposition}
	}
}

func (s *Store) recoverTrailingPartial(frameStart int64) error {
	end, err := s.file.Seek(0, io.SeekEnd)
	if err != nil {
		return fmt.Errorf("%w: seek partial tail: %v", ErrStoreNeedsRecovery, err)
	}
	if end < frameStart {
		return ErrJournalCorrupt
	}
	if err := s.file.Truncate(frameStart); err != nil {
		return fmt.Errorf("%w: truncate partial tail: %v", ErrStoreNeedsRecovery, err)
	}
	if err := s.file.Sync(); err != nil {
		return fmt.Errorf("%w: sync recovered journal: %v", ErrStoreNeedsRecovery, err)
	}
	s.recovery.TrailingPartialDiscardedBytes += end - frameStart
	_, err = s.file.Seek(frameStart, io.SeekStart)
	return err
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	var diff byte
	for i := range a {
		diff |= a[i] ^ b[i]
	}
	return diff == 0
}
