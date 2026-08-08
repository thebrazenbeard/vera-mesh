package inbox

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func testEnvelope() Envelope {
	payload := []byte("first-install note")
	sum := sha256.Sum256(payload)
	return Envelope{
		MessageID:        "00010203-0405-4607-8809-0a0b0c0d0e0f",
		PairID:           "pair-001",
		TrustGeneration:  7,
		SenderNodeID:     "windows-peer",
		RecipientNodeID:  "ds216-peer",
		MessageSchemaID:  "VERA_MESH_NOTE_V1",
		Payload:          payload,
		PayloadByteCount: uint64(len(payload)),
		PayloadSHA256:    hex.EncodeToString(sum[:]),
	}
}

func TestAcceptPersistsBeforeReceiptAndReplays(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	receipt, err := store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if err != nil {
		t.Fatal(err)
	}
	if receipt.Disposition != DurableInboxAccepted {
		t.Fatalf("disposition = %q", receipt.Disposition)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if reopened.Count() != 1 {
		t.Fatalf("count = %d, want 1", reopened.Count())
	}
	got, ok := reopened.Get(env.MessageID)
	if !ok {
		t.Fatal("persisted message not found after reopen")
	}
	if got.Disposition != DurableInboxAccepted {
		t.Fatalf("replayed disposition = %q", got.Disposition)
	}
}

func TestMissingRequiredFieldIsMalformedEnvelope(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	env := testEnvelope()
	env.SenderNodeID = ""
	_, err = store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if !errors.Is(err, ErrMalformedEnvelope) {
		t.Fatalf("err=%v want ErrMalformedEnvelope", err)
	}
	if store.Count() != 0 {
		t.Fatalf("count=%d want0", store.Count())
	}
}

func TestReplayRejectsChecksumValidRecordWithInvalidPayloadBinding(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	env := testEnvelope()
	env.PayloadSHA256 = strings.Repeat("0", 64)
	rec := journalRecord{Version: 1, Envelope: env, Disposition: DurableInboxAccepted}
	payload, err := json.Marshal(rec)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(payload)
	var header [36]byte
	binary.BigEndian.PutUint32(header[:4], uint32(len(payload)))
	copy(header[4:], sum[:])
	data := append(header[:], payload...)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}

	store, err := Open(path)
	if store != nil {
		store.Close()
	}
	if !errors.Is(err, ErrJournalCorrupt) {
		t.Fatalf("err=%v want ErrJournalCorrupt", err)
	}
}

func TestStoreRejectsMalformedMessageIDBeforeJournalWrite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	env := testEnvelope()
	env.MessageID = "uuidv7-like-ish"
	_, err = store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if !errors.Is(err, ErrMalformedMessageID) {
		t.Fatalf("err=%v want ErrMalformedMessageID", err)
	}
	if store.Count() != 0 {
		t.Fatalf("count=%d want0", store.Count())
	}
}

type syncFailFile struct{ journalFile }

func (f syncFailFile) Sync() error { return errors.New("synthetic sync failure") }

func TestSyncFailurePoisonsStoreUntilRecovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	store.file = syncFailFile{journalFile: store.file}
	env := testEnvelope()
	_, err = store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if err == nil {
		t.Fatal("first Accept unexpectedly succeeded")
	}
	_, err = store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if !errors.Is(err, ErrStoreNeedsRecovery) {
		t.Fatalf("second Accept err=%v want ErrStoreNeedsRecovery", err)
	}
}

func TestConcurrentExactRetriesProduceOneDurableRecord(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	trust := TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}

	const workers = 32
	start := make(chan struct{})
	errs := make(chan error, workers)
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer wg.Done()
			<-start
			_, err := store.Accept(trust, env)
			errs <- err
		}()
	}
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("Accept: %v", err)
		}
	}
	if store.Count() != 1 {
		t.Fatalf("count=%d want1", store.Count())
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(path); err != nil {
		t.Fatalf("reopen after concurrent retries: %v", err)
	}
}

func TestTrailingPartialFinalFrameRecoversToLastVerifiedBoundary(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	trust := TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}
	if _, err := store.Accept(trust, env); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.Write([]byte{0, 0}); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer reopened.Close()
	if reopened.Count() != 1 {
		t.Fatalf("count=%d want1", reopened.Count())
	}
	recovery := reopened.Recovery()
	if recovery.TrailingPartialDiscardedBytes != 2 {
		t.Fatalf("discarded=%d want2", recovery.TrailingPartialDiscardedBytes)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() != before.Size() {
		t.Fatalf("journal size after recovery=%d want=%d", after.Size(), before.Size())
	}
}

func TestAcceptAfterCloseReturnsTypedErrorInsteadOfPanicking(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	_, err = store.Accept(TrustContext{Active: true, PairID: env.PairID, TrustGeneration: env.TrustGeneration}, env)
	if !errors.Is(err, ErrStoreClosed) {
		t.Fatalf("err=%v want ErrStoreClosed", err)
	}
}
