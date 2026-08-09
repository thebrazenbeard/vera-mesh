package trust

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func digest(label string) string {
	sum := sha256.Sum256([]byte(label))
	return fmt.Sprintf("%x", sum[:])
}

func binding(cert string, generation uint64) Binding {
	return Binding{AuthenticatedPeerNodeID: "peer-a", LocalNodeID: "local", PairID: "pair-a", TrustGeneration: generation, CertificateDERSHA256: digest(cert), KeyIdentitySHA256: digest("key-k")}
}

func TestOrdinaryRevokeSerializesAgainstDurableAdmission(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if err := a.Activate(binding("cert-a", 1)); err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	admitDone := make(chan error, 1)
	go func() {
		admitDone <- a.WithDurableAdmission(AdmissionRequest(binding("cert-a", 1)), func(Binding) error { close(entered); <-release; return nil })
	}()
	<-entered
	revokeDone := make(chan error, 1)
	go func() {
		revokeDone <- a.Revoke(RevokeRequest{PairID: "pair-a", CertificateDERSHA256: digest("cert-a"), Reason: RevokeOrdinary})
	}()
	deadline := time.After(time.Second)
	for {
		err := a.WithDurableAdmission(AdmissionRequest(binding("cert-a", 1)), func(Binding) error { return nil })
		if errors.Is(err, ErrRevoking) {
			break
		}
		if !errors.Is(err, ErrAdmissionBusy) {
			t.Fatalf("during revoke admission err=%v", err)
		}
		select {
		case <-deadline:
			t.Fatal("revoke never established REVOKING barrier")
		default:
		}
	}
	select {
	case err := <-revokeDone:
		t.Fatalf("revoke completed before in-flight decision: %v", err)
	default:
	}
	close(release)
	if err := <-admitDone; err != nil {
		t.Fatal(err)
	}
	if err := <-revokeDone; err != nil {
		t.Fatal(err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(binding("cert-a", 1)), func(Binding) error { return nil }); !errors.Is(err, ErrRevokedIdentity) {
		t.Fatalf("post revoke err=%v", err)
	}
}

func TestDurableRevokeWitnessRecoversFailClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(binding("cert-a", 1)); err != nil {
		t.Fatal(err)
	}
	r, err := a.BeginRevoke(RevokeRequest{PairID: "pair-a", CertificateDERSHA256: digest("cert-a"), Reason: RevokeOrdinary})
	if err != nil {
		t.Fatal(err)
	}
	if r == nil {
		t.Fatal("nil revocation")
	}
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if err := reopened.WithDurableAdmission(AdmissionRequest(binding("cert-a", 1)), func(Binding) error { return nil }); !errors.Is(err, ErrRevoking) {
		t.Fatalf("recovered admission err=%v", err)
	}
}

func TestRepairRequiresNewCertificateAndHigherGeneration(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if err := a.Activate(binding("cert-a", 7)); err != nil {
		t.Fatal(err)
	}
	if err := a.Revoke(RevokeRequest{PairID: "pair-a", CertificateDERSHA256: digest("cert-a"), Reason: RevokeOrdinary}); err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(binding("cert-a", 8)); !errors.Is(err, ErrRevokedIdentity) {
		t.Fatalf("same cert repair err=%v", err)
	}
	if err := a.Activate(binding("cert-b", 7)); !errors.Is(err, ErrStaleGeneration) {
		t.Fatalf("same generation repair err=%v", err)
	}
	if err := a.Activate(binding("cert-b", 8)); err != nil {
		t.Fatalf("new cert/gen repair: %v", err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(binding("cert-a", 7)), func(Binding) error { return nil }); err == nil {
		t.Fatal("stale old binding admitted")
	}
	if err := a.WithDurableAdmission(AdmissionRequest(binding("cert-b", 8)), func(Binding) error { return nil }); err != nil {
		t.Fatal(err)
	}
}

func TestKeyCompromisedBarrierDrainsAliases(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	a1 := binding("cert-a", 1)
	b1 := a1
	b1.PairID = "pair-b"
	b1.AuthenticatedPeerNodeID = "peer-b"
	b1.CertificateDERSHA256 = digest("cert-b")
	if err := a.Activate(a1); err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(b1); err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- a.WithDurableAdmission(AdmissionRequest(b1), func(Binding) error { close(entered); <-release; return nil })
	}()
	<-entered
	revokeDone := make(chan error, 1)
	go func() {
		revokeDone <- a.Revoke(RevokeRequest{PairID: "pair-a", CertificateDERSHA256: digest("cert-a"), KeyIdentitySHA256: digest("key-k"), Reason: RevokeKeyCompromised})
	}()
	deadline := time.After(time.Second)
	for {
		alias := b1
		alias.PairID = "pair-c"
		alias.CertificateDERSHA256 = digest("cert-c")
		err := a.Activate(alias)
		if errors.Is(err, ErrKeyCompromised) || errors.Is(err, ErrRevoking) {
			break
		}
		select {
		case <-deadline:
			t.Fatalf("key barrier not established; last=%v", err)
		default:
		}
	}
	select {
	case err := <-revokeDone:
		t.Fatalf("key revoke finished before alias drain: %v", err)
	default:
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if err := <-revokeDone; err != nil {
		t.Fatal(err)
	}
	alias := b1
	alias.PairID = "pair-c"
	alias.CertificateDERSHA256 = digest("cert-c")
	alias.TrustGeneration = 2
	if err := a.Activate(alias); !errors.Is(err, ErrKeyCompromised) {
		t.Fatalf("same-key alias activation err=%v", err)
	}
}

func TestConcurrentCallbacksForSamePeerDoNotBothEnter(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b := binding("cert-a", 1)
	if err := a.Activate(b); err != nil {
		t.Fatal(err)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		_ = a.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { close(entered); <-release; return nil })
	}()
	<-entered
	if err := a.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { return nil }); !errors.Is(err, ErrAdmissionBusy) {
		t.Fatalf("second admission err=%v", err)
	}
	close(release)
	wg.Wait()
}

func TestUnknownDurableDecisionPreventsRevokeSuccess(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b := binding("cert-a", 1)
	if err := a.Activate(b); err != nil {
		t.Fatal(err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { return ErrDurableDecisionUnknown }); !errors.Is(err, ErrDurableDecisionUnknown) {
		t.Fatalf("admission err=%v want ErrDurableDecisionUnknown", err)
	}
	if err := a.Revoke(RevokeRequest{PairID: b.PairID, CertificateDERSHA256: b.CertificateDERSHA256, Reason: RevokeOrdinary}); !errors.Is(err, ErrDurableDecisionUnknown) {
		t.Fatalf("revoke err=%v want ErrDurableDecisionUnknown", err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { return nil }); !errors.Is(err, ErrRevoking) {
		t.Fatalf("post-unknown revoke barrier err=%v want ErrRevoking", err)
	}
}

func TestRecoveredRevokeCanResumeToDurableTombstone(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	b := binding("cert-a", 1)
	if err := a.Activate(b); err != nil {
		t.Fatal(err)
	}
	if _, err := a.BeginRevoke(RevokeRequest{PairID: b.PairID, CertificateDERSHA256: b.CertificateDERSHA256, Reason: RevokeOrdinary}); err != nil {
		t.Fatal(err)
	}
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	r, err := reopened.BeginRevoke(RevokeRequest{PairID: b.PairID, CertificateDERSHA256: b.CertificateDERSHA256, Reason: RevokeOrdinary})
	if err != nil {
		t.Fatalf("resume BeginRevoke: %v", err)
	}
	if err := r.Complete(); err != nil {
		t.Fatalf("resume Complete: %v", err)
	}
	if err := reopened.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { return nil }); !errors.Is(err, ErrRevokedIdentity) {
		t.Fatalf("post-resume admission err=%v", err)
	}
}

type readbackFailFile struct{ trustJournalFile }

func (f readbackFailFile) Read(p []byte) (int, error) {
	return 0, errors.New("synthetic readback failure")
}

func TestRevokeRequiresExactPersistedFrameReadbackBeforeSuccess(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b := binding("cert-a", 1)
	if err := a.Activate(b); err != nil {
		t.Fatal(err)
	}

	a.file = readbackFailFile{trustJournalFile: a.file}
	err = a.Revoke(RevokeRequest{PairID: b.PairID, CertificateDERSHA256: b.CertificateDERSHA256, Reason: RevokeOrdinary})
	if !errors.Is(err, ErrTrustIntegrity) {
		t.Fatalf("revoke err=%v want ErrTrustIntegrity", err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(b), func(Binding) error { return nil }); !errors.Is(err, ErrTrustIntegrity) {
		t.Fatalf("post-readback-failure admission err=%v want ErrTrustIntegrity", err)
	}
}

func TestOrdinaryRevokeCanEscalateToKeyCompromisedDomainBarrier(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	a1 := binding("cert-a", 1)
	b1 := a1
	b1.PairID = "pair-b"
	b1.AuthenticatedPeerNodeID = "peer-b"
	b1.CertificateDERSHA256 = digest("cert-b")
	if err := a.Activate(a1); err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(b1); err != nil {
		t.Fatal(err)
	}
	ordinary, err := a.BeginRevoke(RevokeRequest{PairID: a1.PairID, CertificateDERSHA256: a1.CertificateDERSHA256, Reason: RevokeOrdinary})
	if err != nil {
		t.Fatal(err)
	}
	compromise, err := a.BeginRevoke(RevokeRequest{PairID: a1.PairID, CertificateDERSHA256: a1.CertificateDERSHA256, KeyIdentitySHA256: a1.KeyIdentitySHA256, Reason: RevokeKeyCompromised})
	if err != nil {
		t.Fatalf("escalate ordinary->key compromise: %v", err)
	}
	if err := a.WithDurableAdmission(AdmissionRequest(b1), func(Binding) error { return nil }); !errors.Is(err, ErrRevoking) {
		t.Fatalf("same-key alias after escalation err=%v want ErrRevoking", err)
	}
	if err := ordinary.Complete(); err != nil {
		t.Fatalf("ordinary complete after escalation: %v", err)
	}
	if err := compromise.Complete(); err != nil {
		t.Fatalf("key compromise complete: %v", err)
	}
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}

	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	alias := b1
	alias.PairID = "pair-c"
	alias.CertificateDERSHA256 = digest("cert-c")
	alias.TrustGeneration = 2
	if err := reopened.Activate(alias); !errors.Is(err, ErrKeyCompromised) {
		t.Fatalf("same-key alias after restart err=%v want ErrKeyCompromised", err)
	}
}

func TestCompletedOrdinaryRevokeCanLaterStrengthenToKeyCompromised(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	a1 := binding("cert-a", 1)
	b1 := a1
	b1.PairID = "pair-b"
	b1.AuthenticatedPeerNodeID = "peer-b"
	b1.CertificateDERSHA256 = digest("cert-b")
	if err := a.Activate(a1); err != nil {
		t.Fatal(err)
	}
	if err := a.Activate(b1); err != nil {
		t.Fatal(err)
	}
	if err := a.Revoke(RevokeRequest{PairID: a1.PairID, CertificateDERSHA256: a1.CertificateDERSHA256, Reason: RevokeOrdinary}); err != nil {
		t.Fatal(err)
	}
	if err := a.Revoke(RevokeRequest{PairID: a1.PairID, CertificateDERSHA256: a1.CertificateDERSHA256, KeyIdentitySHA256: a1.KeyIdentitySHA256, Reason: RevokeKeyCompromised}); err != nil {
		t.Fatalf("post-revoked strengthen: %v", err)
	}
	alias := b1
	alias.PairID = "pair-c"
	alias.CertificateDERSHA256 = digest("cert-c")
	alias.TrustGeneration = 2
	if err := a.Activate(alias); !errors.Is(err, ErrKeyCompromised) {
		t.Fatalf("same-key alias after strengthen err=%v want ErrKeyCompromised", err)
	}
}

func TestExactRevokeRetryAfterDurableTombstoneIsIdempotentNoSecondWrite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trust.journal")
	a, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b := binding("cert-a", 1)
	if err := a.Activate(b); err != nil {
		t.Fatal(err)
	}
	req := RevokeRequest{PairID: b.PairID, CertificateDERSHA256: b.CertificateDERSHA256, Reason: RevokeOrdinary}
	if err := a.Revoke(req); err != nil {
		t.Fatal(err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Revoke(req); err != nil {
		t.Fatalf("idempotent retry: %v", err)
	}
	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() != before.Size() {
		t.Fatalf("retry changed journal size: before=%d after=%d", before.Size(), after.Size())
	}
}

func TestActivateRejectsMalformedCertificateOrKeyDigest(t *testing.T) {
	a, err := Open(filepath.Join(t.TempDir(), "trust.journal"))
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b := binding("cert-a", 1)
	b.CertificateDERSHA256 = "ABCDEF"
	if err := a.Activate(b); !errors.Is(err, ErrInvalidBinding) {
		t.Fatalf("malformed cert digest err=%v want ErrInvalidBinding", err)
	}
	b = binding("cert-a", 1)
	b.KeyIdentitySHA256 = "not-a-sha256"
	if err := a.Activate(b); !errors.Is(err, ErrInvalidBinding) {
		t.Fatalf("malformed key digest err=%v want ErrInvalidBinding", err)
	}
}
