package inbox

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/thebrazenbeard/vera-mesh/reference/windows-go/internal/quota"
	meshtrust "github.com/thebrazenbeard/vera-mesh/reference/windows-go/internal/trust"
)

func testDigest(label string) string {
	sum := sha256.Sum256([]byte(label))
	return fmt.Sprintf("%x", sum[:])
}

func two024Envelope(messageID string) Envelope {
	payload := []byte("first-install note")
	sum := sha256.Sum256(payload)
	return Envelope{
		MessageID:        messageID,
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

func two024Binding(env Envelope) meshtrust.Binding {
	return meshtrust.Binding{
		AuthenticatedPeerNodeID: env.SenderNodeID,
		LocalNodeID:             env.RecipientNodeID,
		PairID:                  env.PairID,
		TrustGeneration:         env.TrustGeneration,
		CertificateDERSHA256:    testDigest("cert-a"),
		KeyIdentitySHA256:       testDigest("key-a"),
	}
}

func two024Authority(t *testing.T, env Envelope) *meshtrust.Authority {
	t.Helper()
	a, err := meshtrust.Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(two024Binding(env)); err != nil {
		a.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = a.Close() })
	return a
}

func two024Tracker(t *testing.T, maxCount, maxBytes uint64) *quota.Tracker {
	t.Helper()
	p, err := quota.NewQualificationProfile(maxCount, maxBytes)
	if err != nil {
		t.Fatal(err)
	}
	return quota.NewTracker(p)
}

func TestTwo024AcceptedReplayAtFullRequiresFreshTrustAndZeroSecondCharge(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 1, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()

	req := meshtrust.AdmissionRequest(two024Binding(env))
	first, err := s.Accept(req, env)
	if err != nil {
		t.Fatal(err)
	}
	if first.Disposition != DurableInboxAccepted {
		t.Fatalf("first=%+v", first)
	}
	before := q.Status()
	if before.BindingState != quota.Full {
		t.Fatalf("status=%+v want FULL", before)
	}

	replay, err := s.Accept(req, env)
	if err != nil {
		t.Fatal(err)
	}
	if replay != first {
		t.Fatalf("replay=%+v first=%+v", replay, first)
	}
	if after := q.Status(); after != before {
		t.Fatalf("replay charged quota: before=%+v after=%+v", before, after)
	}

	if err := a.Revoke(meshtrust.RevokeRequest{PairID: env.PairID, CertificateDERSHA256: testDigest("cert-a"), Reason: meshtrust.RevokeOrdinary}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Accept(req, env); !errors.Is(err, meshtrust.ErrRevokedIdentity) {
		t.Fatalf("revoked replay err=%v", err)
	}
}

func TestTwo024UniqueAtFullRejectsBeforeJournalMutation(t *testing.T) {
	env1 := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	env2 := two024Envelope("10010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env1)
	q := two024Tracker(t, 1, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	req := meshtrust.AdmissionRequest(two024Binding(env1))
	if _, err := s.Accept(req, env1); err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(path)
	before := st.Size()

	receipt, err := s.Accept(req, env2)
	if !errors.Is(err, quota.ErrLogicalQuotaFull) {
		t.Fatalf("err=%v", err)
	}
	if receipt != (Receipt{}) {
		t.Fatalf("quota rejection emitted receipt: %+v", receipt)
	}
	st, _ = os.Stat(path)
	if st.Size() != before {
		t.Fatalf("quota rejection mutated journal: before=%d after=%d", before, st.Size())
	}
	if s.Count() != 1 {
		t.Fatalf("count=%d want1", s.Count())
	}
}

func TestTwo024ReopenReconstructsExactCommittedFrameQuota(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q1 := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q1)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Accept(meshtrust.AdmissionRequest(two024Binding(env)), env); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}

	q2 := two024Tracker(t, 3, 1<<20)
	reopened, err := OpenForAdmission(path, a, q2)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	got := q2.Status()
	if got.CurrentCount != 1 || got.CurrentCommittedBytes != uint64(st.Size()) {
		t.Fatalf("restored quota=%+v journal_size=%d", got, st.Size())
	}
}

type two024GateSyncFile struct {
	journalFile
	once    sync.Once
	entered chan struct{}
	release chan struct{}
}

func (f *two024GateSyncFile) Sync() error {
	f.once.Do(func() { close(f.entered) })
	<-f.release
	return f.journalFile.Sync()
}

func TestTwo024RevokeWaitsThroughInboxSyncDecision(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	gate := &two024GateSyncFile{journalFile: s.file, entered: make(chan struct{}), release: make(chan struct{})}
	s.file = gate
	req := meshtrust.AdmissionRequest(two024Binding(env))
	acceptDone := make(chan error, 1)
	go func() { _, err := s.Accept(req, env); acceptDone <- err }()
	<-gate.entered

	revokeDone := make(chan error, 1)
	go func() {
		revokeDone <- a.Revoke(meshtrust.RevokeRequest{PairID: env.PairID, CertificateDERSHA256: testDigest("cert-a"), Reason: meshtrust.RevokeOrdinary})
	}()
	deadline := time.After(time.Second)
	for {
		_, err := s.Accept(req, two024Envelope("10010203-0405-4607-8809-0a0b0c0d0e0f"))
		if errors.Is(err, meshtrust.ErrRevoking) {
			break
		}
		if !errors.Is(err, meshtrust.ErrAdmissionBusy) {
			t.Fatalf("second accept err=%v", err)
		}
		select {
		case <-deadline:
			t.Fatal("revocation barrier not established")
		default:
		}
	}
	select {
	case err := <-revokeDone:
		t.Fatalf("revoke completed before Sync decision: %v", err)
	default:
	}
	close(gate.release)
	if err := <-acceptDone; err != nil {
		t.Fatal(err)
	}
	if err := <-revokeDone; err != nil {
		t.Fatal(err)
	}
}

type two024FailSyncFile struct{ journalFile }

func (f two024FailSyncFile) Sync() error { return errors.New("synthetic ambiguous sync failure") }

func TestTwo024SyncFailureBlocksRevokeSuccessAsUnknown(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	s.file = two024FailSyncFile{journalFile: s.file}
	_, err = s.Accept(meshtrust.AdmissionRequest(two024Binding(env)), env)
	if !errors.Is(err, meshtrust.ErrDurableDecisionUnknown) {
		t.Fatalf("accept err=%v", err)
	}
	if err := a.Revoke(meshtrust.RevokeRequest{PairID: env.PairID, CertificateDERSHA256: testDigest("cert-a"), Reason: meshtrust.RevokeOrdinary}); !errors.Is(err, meshtrust.ErrDurableDecisionUnknown) {
		t.Fatalf("revoke err=%v", err)
	}
}

func TestBaselineMissingRequiredFieldIsMalformedBeforeWrite(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	env.SenderNodeID = ""
	_, err = s.Accept(meshtrust.AdmissionRequest(two024Binding(two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f"))), env)
	if !errors.Is(err, ErrMalformedEnvelope) {
		t.Fatalf("err=%v want ErrMalformedEnvelope", err)
	}
	st, _ := os.Stat(path)
	if st.Size() != 0 || s.Count() != 0 {
		t.Fatalf("mutation size=%d count=%d", st.Size(), s.Count())
	}
}

func TestBaselineReplayRejectsChecksumValidRecordWithInvalidPayloadBinding(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	env.PayloadSHA256 = "0000000000000000000000000000000000000000000000000000000000000000"
	rec := journalRecord{Version: 1, Envelope: env, Disposition: DurableInboxAccepted}
	frame, err := prepareJournalFrame(rec)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, frame, 0o600); err != nil {
		t.Fatal(err)
	}
	s, err := Open(path)
	if s != nil {
		_ = s.Close()
	}
	if !errors.Is(err, ErrJournalCorrupt) {
		t.Fatalf("err=%v want ErrJournalCorrupt", err)
	}
}

func TestBaselineMalformedMessageIDRejectsBeforeWrite(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	env.MessageID = "uuidv7-like-ish"
	_, err = s.Accept(meshtrust.AdmissionRequest(two024Binding(two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f"))), env)
	if !errors.Is(err, ErrMalformedMessageID) {
		t.Fatalf("err=%v", err)
	}
	st, _ := os.Stat(path)
	if st.Size() != 0 {
		t.Fatalf("size=%d want0", st.Size())
	}
}

func TestBaselineSyncFailurePoisonsStoreUntilRecovery(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	s.file = two024FailSyncFile{journalFile: s.file}
	req := meshtrust.AdmissionRequest(two024Binding(env))
	if _, err = s.Accept(req, env); !errors.Is(err, meshtrust.ErrDurableDecisionUnknown) {
		t.Fatalf("first err=%v", err)
	}
	if _, err = s.Accept(req, env); !errors.Is(err, ErrStoreNeedsRecovery) {
		t.Fatalf("second err=%v want recovery", err)
	}
}

func TestBaselineConcurrentExactRetriesProduceOneDurableRecord(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	req := meshtrust.AdmissionRequest(two024Binding(env))
	const workers = 32
	start := make(chan struct{})
	errs := make(chan error, workers)
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() { defer wg.Done(); <-start; _, e := s.Accept(req, env); errs <- e }()
	}
	close(start)
	wg.Wait()
	close(errs)
	for e := range errs {
		if e != nil && !errors.Is(e, meshtrust.ErrAdmissionBusy) {
			t.Fatalf("unexpected err=%v", e)
		}
	}
	// Any callers that lost the single peer admission race can retry after the
	// winning durable decision and receive the same disposition under fresh trust.
	if _, err := s.Accept(req, env); err != nil {
		t.Fatal(err)
	}
	if s.Count() != 1 {
		t.Fatalf("count=%d want1", s.Count())
	}
	if q.Status().CurrentCount != 1 {
		t.Fatalf("quota count=%d want1", q.Status().CurrentCount)
	}
}

func TestBaselineTrailingPartialFinalFrameRecoversToLastVerifiedBoundary(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Accept(meshtrust.AdmissionRequest(two024Binding(env)), env); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	before, _ := os.Stat(path)
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write([]byte{0, 0}); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	r, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if r.Count() != 1 || r.Recovery().TrailingPartialDiscardedBytes != 2 {
		t.Fatalf("count=%d recovery=%+v", r.Count(), r.Recovery())
	}
	after, _ := os.Stat(path)
	if after.Size() != before.Size() {
		t.Fatalf("after=%d before=%d", after.Size(), before.Size())
	}
}

func TestBaselineAcceptAfterCloseReturnsTypedError(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	s, err := OpenForAdmission(filepath.Join(t.TempDir(), "inbox.journal"), a, q)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	_, err = s.Accept(meshtrust.AdmissionRequest(two024Binding(env)), env)
	if !errors.Is(err, ErrStoreClosed) {
		t.Fatalf("err=%v", err)
	}
}

func TestBaselineCorruptCompleteLengthHeaderFailsClosedWithoutTruncation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	env1 := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	env2 := two024Envelope("10010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env1)
	q := two024Tracker(t, 3, 1<<20)
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	req := meshtrust.AdmissionRequest(two024Binding(env1))
	if _, err := s.Accept(req, env1); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Accept(req, env2); err != nil {
		t.Fatal(err)
	}
	_ = s.Close()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	before := int64(len(data))
	binary.BigEndian.PutUint32(data[journalPayloadLengthOffset:journalPayloadLengthOffset+4], uint32(len(data)-journalHeaderSize+1))
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	r, err := Open(path)
	if r != nil {
		_ = r.Close()
	}
	if !errors.Is(err, ErrJournalCorrupt) {
		t.Fatalf("err=%v", err)
	}
	st, _ := os.Stat(path)
	if st.Size() != before {
		t.Fatalf("truncated size=%d want=%d", st.Size(), before)
	}
}

func TestBaselineVerifiedHeaderPartialPayloadRecoversTailOnly(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.journal")
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 3, 1<<20)
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Accept(meshtrust.AdmissionRequest(two024Binding(env)), env); err != nil {
		t.Fatal(err)
	}
	_ = s.Close()
	before, _ := os.Stat(path)
	env2 := two024Envelope("20010203-0405-4607-8809-0a0b0c0d0e0f")
	frame, err := prepareJournalFrame(journalRecord{Version: 1, Envelope: env2, Disposition: DurableInboxAccepted})
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = f.Write(frame[:journalHeaderSize+5]); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	r, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if got := r.Recovery().TrailingPartialDiscardedBytes; got != int64(journalHeaderSize+5) {
		t.Fatalf("discarded=%d", got)
	}
	after, _ := os.Stat(path)
	if after.Size() != before.Size() {
		t.Fatalf("size=%d want=%d", after.Size(), before.Size())
	}
}

func TestBaselineOversizeJournalRecordRejectedBeforeWriteAndStoreRemainsUsable(t *testing.T) {
	normal := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, normal)
	q := two024Tracker(t, 3, 64<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	oversize := normal
	oversize.Payload = make([]byte, 13<<20)
	sum := sha256.Sum256(oversize.Payload)
	oversize.PayloadByteCount = uint64(len(oversize.Payload))
	oversize.PayloadSHA256 = hex.EncodeToString(sum[:])
	_, err = s.Accept(meshtrust.AdmissionRequest(two024Binding(normal)), oversize)
	if !errors.Is(err, ErrJournalFrameTooLarge) {
		t.Fatalf("err=%v", err)
	}
	st, _ := os.Stat(path)
	if st.Size() != 0 {
		t.Fatalf("size=%d", st.Size())
	}
	if _, err := s.Accept(meshtrust.AdmissionRequest(two024Binding(normal)), normal); err != nil {
		t.Fatalf("store poisoned: %v", err)
	}
}

func TestBaselineEnvelopeNodeSubstitutionRejectsBeforeWrite(t *testing.T) {
	base := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, base)
	q := two024Tracker(t, 3, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	req := meshtrust.AdmissionRequest(two024Binding(base))
	badSender := base
	badSender.SenderNodeID = "peer-B"
	if _, err := s.Accept(req, badSender); !errors.Is(err, ErrAuthenticatedPeerNodeMismatch) {
		t.Fatalf("sender err=%v", err)
	}
	badRecipient := base
	badRecipient.RecipientNodeID = "local-B"
	if _, err := s.Accept(req, badRecipient); !errors.Is(err, ErrLocalRecipientNodeMismatch) {
		t.Fatalf("recipient err=%v", err)
	}
	st, _ := os.Stat(path)
	if st.Size() != 0 || s.Count() != 0 {
		t.Fatalf("mutation size=%d count=%d", st.Size(), s.Count())
	}
}

func TestBaselineEnvelopePairAndGenerationMismatchRejectBeforeWrite(t *testing.T) {
	base := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, base)
	q := two024Tracker(t, 10, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	req := meshtrust.AdmissionRequest(two024Binding(base))

	badPair := base
	badPair.PairID = "pair-other"
	if _, err := s.Accept(req, badPair); !errors.Is(err, ErrPairMismatch) {
		t.Fatalf("pair mismatch err=%v want ErrPairMismatch", err)
	}
	badGen := base
	badGen.TrustGeneration++
	if _, err := s.Accept(req, badGen); !errors.Is(err, ErrTrustGenerationMismatch) {
		t.Fatalf("generation mismatch err=%v want ErrTrustGenerationMismatch", err)
	}
	if got := s.Count(); got != 0 {
		t.Fatalf("count=%d want0", got)
	}
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if st.Size() != 0 {
		t.Fatalf("journal size=%d want0", st.Size())
	}
}

func TestBaselineMessageIDConflictRejectsWithoutSecondChargeOrAppend(t *testing.T) {
	env := two024Envelope("00010203-0405-4607-8809-0a0b0c0d0e0f")
	a := two024Authority(t, env)
	q := two024Tracker(t, 10, 1<<20)
	path := filepath.Join(t.TempDir(), "inbox.journal")
	s, err := OpenForAdmission(path, a, q)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	req := meshtrust.AdmissionRequest(two024Binding(env))
	if _, err := s.Accept(req, env); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	beforeStatus := q.Status()

	conflict := env
	conflict.Payload = []byte("different")
	sum := sha256.Sum256(conflict.Payload)
	conflict.PayloadByteCount = uint64(len(conflict.Payload))
	conflict.PayloadSHA256 = hex.EncodeToString(sum[:])
	if _, err := s.Accept(req, conflict); !errors.Is(err, ErrMessageIDConflict) {
		t.Fatalf("conflict err=%v want ErrMessageIDConflict", err)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() != before.Size() {
		t.Fatalf("conflict appended bytes before=%d after=%d", before.Size(), after.Size())
	}
	afterStatus := q.Status()
	if afterStatus.CurrentCount != beforeStatus.CurrentCount || afterStatus.CurrentCommittedBytes != beforeStatus.CurrentCommittedBytes {
		t.Fatalf("conflict changed quota before=%+v after=%+v", beforeStatus, afterStatus)
	}
}
