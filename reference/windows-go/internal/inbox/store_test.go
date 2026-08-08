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

func testTrust(env Envelope) TrustContext {
	return TrustContext{
		Active:                  true,
		PairID:                  env.PairID,
		TrustGeneration:         env.TrustGeneration,
		AuthenticatedPeerNodeID: env.SenderNodeID,
		LocalNodeID:             env.RecipientNodeID,
	}
}

func TestAcceptPersistsBeforeReceiptAndReplays(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	receipt, err := store.Accept(testTrust(env), env)
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
	trust := testTrust(env)
	env.SenderNodeID = ""
	_, err = store.Accept(trust, env)
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
	header := makeJournalHeader(payload)
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
	_, err = store.Accept(testTrust(env), env)
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
	_, err = store.Accept(testTrust(env), env)
	if err == nil {
		t.Fatal("first Accept unexpectedly succeeded")
	}
	_, err = store.Accept(testTrust(env), env)
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
	trust := testTrust(env)

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
	trust := testTrust(env)
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
	_, err = store.Accept(testTrust(env), env)
	if !errors.Is(err, ErrStoreClosed) {
		t.Fatalf("err=%v want ErrStoreClosed", err)
	}
}

func TestCorruptCompleteLengthHeaderFailsClosedWithoutTruncation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env1 := testEnvelope()
	env2 := testEnvelope()
	env2.MessageID = "10010203-0405-4607-8809-0a0b0c0d0e0f"
	trust := testTrust(env1)
	if _, err := store.Accept(trust, env1); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Accept(trust, env2); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	before := int64(len(data))
	binary.BigEndian.PutUint32(data[journalPayloadLengthOffset:journalPayloadLengthOffset+4], uint32(len(data)-journalHeaderSize+1))
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if reopened != nil {
		reopened.Close()
	}
	if !errors.Is(err, ErrJournalCorrupt) {
		t.Fatalf("err=%v want ErrJournalCorrupt", err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != before {
		t.Fatalf("corrupt complete header was destructively truncated: size=%d want=%d", st.Size(), before)
	}
}

func TestVerifiedCompleteHeaderWithPartialFinalPayloadRecoversTailOnly(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	env := testEnvelope()
	trust := testTrust(env)
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
	env2 := testEnvelope()
	env2.MessageID = "20010203-0405-4607-8809-0a0b0c0d0e0f"
	rec := journalRecord{Version: 1, Envelope: env2, Disposition: DurableInboxAccepted}
	payload, err := json.Marshal(rec)
	if err != nil {
		t.Fatal(err)
	}
	header := makeJournalHeader(payload)
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.Write(header[:]); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Write(payload[:5]); err != nil {
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
	if reopened.Recovery().TrailingPartialDiscardedBytes != int64(journalHeaderSize+5) {
		t.Fatalf("discarded=%d want=%d", reopened.Recovery().TrailingPartialDiscardedBytes, journalHeaderSize+5)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() != before.Size() {
		t.Fatalf("size=%d want=%d", after.Size(), before.Size())
	}
}

func TestOversizeJournalRecordRejectedBeforeWriteAndStoreRemainsUsable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	oversize := testEnvelope()
	oversize.Payload = make([]byte, 13<<20)
	sum := sha256.Sum256(oversize.Payload)
	oversize.PayloadByteCount = uint64(len(oversize.Payload))
	oversize.PayloadSHA256 = hex.EncodeToString(sum[:])
	trust := testTrust(oversize)
	_, err = store.Accept(trust, oversize)
	if !errors.Is(err, ErrJournalFrameTooLarge) {
		t.Fatalf("err=%v want ErrJournalFrameTooLarge", err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != 0 {
		t.Fatalf("oversize deterministic rejection mutated journal: size=%d want0", st.Size())
	}

	normal := testEnvelope()
	if _, err := store.Accept(trust, normal); err != nil {
		t.Fatalf("store was poisoned by deterministic oversize rejection: %v", err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(path)
	if err != nil {
		t.Fatalf("Open after normal record: %v", err)
	}
	defer reopened.Close()
	if reopened.Count() != 1 {
		t.Fatalf("count=%d want1", reopened.Count())
	}
}

func TestAcceptRejectsSenderNotBoundToAuthenticatedPeer(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	env := testEnvelope()
	trust := TrustContext{
		Active:                  true,
		PairID:                  env.PairID,
		TrustGeneration:         env.TrustGeneration,
		AuthenticatedPeerNodeID: "peer-A",
		LocalNodeID:             env.RecipientNodeID,
	}
	env.SenderNodeID = "peer-B"
	_, err = store.Accept(trust, env)
	if !errors.Is(err, ErrAuthenticatedPeerNodeMismatch) {
		t.Fatalf("err=%v want ErrAuthenticatedPeerNodeMismatch", err)
	}
	if store.Count() != 0 {
		t.Fatalf("count=%d want0", store.Count())
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != 0 {
		t.Fatalf("sender mismatch mutated journal: size=%d want0", st.Size())
	}
}

func TestAcceptRejectsRecipientNotBoundToLocalNode(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	env := testEnvelope()
	trust := TrustContext{
		Active:                  true,
		PairID:                  env.PairID,
		TrustGeneration:         env.TrustGeneration,
		AuthenticatedPeerNodeID: env.SenderNodeID,
		LocalNodeID:             "local-A",
	}
	env.RecipientNodeID = "local-B"
	_, err = store.Accept(trust, env)
	if !errors.Is(err, ErrLocalRecipientNodeMismatch) {
		t.Fatalf("err=%v want ErrLocalRecipientNodeMismatch", err)
	}
	if store.Count() != 0 {
		t.Fatalf("count=%d want0", store.Count())
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != 0 {
		t.Fatalf("recipient mismatch mutated journal: size=%d want0", st.Size())
	}
}

func TestAcceptRejectsMissingTrustedNodeContextBeforeJournalWrite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	env := testEnvelope()
	trust := TrustContext{
		Active:          true,
		PairID:          env.PairID,
		TrustGeneration: env.TrustGeneration,
	}
	_, err = store.Accept(trust, env)
	if !errors.Is(err, ErrTrustedNodeContextMissing) {
		t.Fatalf("err=%v want ErrTrustedNodeContextMissing", err)
	}
	if store.Count() != 0 {
		t.Fatalf("count=%d want0", store.Count())
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != 0 {
		t.Fatalf("missing trusted node context mutated journal: size=%d want0", st.Size())
	}
}
