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

var (
	ErrTrustInactive           = errors.New("trust inactive")
	ErrPairMismatch            = errors.New("pair mismatch")
	ErrTrustGenerationMismatch = errors.New("trust generation mismatch")
	ErrPayloadDigestMismatch   = errors.New("payload digest mismatch")
	ErrMessageIDConflict       = errors.New("message id conflict")
	ErrJournalCorrupt          = errors.New("journal corrupt")
	ErrMalformedEnvelope       = errors.New("malformed envelope")
	ErrStoreNeedsRecovery      = errors.New("store needs recovery")
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

type TrustContext struct {
	Active          bool
	PairID          string
	TrustGeneration uint64
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
		s.poisoned = true
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
	return validateDurableBinding(env)
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
	if len(payload) > int(^uint32(0)) {
		return ErrJournalCorrupt
	}
	sum := sha256.Sum256(payload)
	var header [36]byte
	binary.BigEndian.PutUint32(header[:4], uint32(len(payload)))
	copy(header[4:], sum[:])
	if _, err := s.file.Write(header[:]); err != nil {
		return err
	}
	if _, err := s.file.Write(payload); err != nil {
		return err
	}
	return s.file.Sync()
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
		var header [36]byte
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
		size := binary.BigEndian.Uint32(header[:4])
		if size > 16<<20 { // local journal corruption guard, not a Mesh protocol resource limit
			return ErrJournalCorrupt
		}
		payload := make([]byte, size)
		if _, err := io.ReadFull(s.file, payload); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return s.recoverTrailingPartial(frameStart)
			}
			return err
		}
		want := header[4:]
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
