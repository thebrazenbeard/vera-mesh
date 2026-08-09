package quota

import (
	"errors"
	"testing"
)

func TestCountAndByteBoundariesAndRestore(t *testing.T) {
	p, err := NewQualificationProfile(2, 250)
	if err != nil {
		t.Fatal(err)
	}
	q := NewTracker(p)
	if err := q.CheckAndCommit(100); err != nil {
		t.Fatal(err)
	}
	if err := q.CheckAndCommit(120); err != nil {
		t.Fatal(err)
	}
	if err := q.Check(1); !errors.Is(err, ErrLogicalQuotaFull) {
		t.Fatalf("count full err=%v", err)
	}
	s := q.Status()
	if s.CurrentCount != 2 || s.CurrentCommittedBytes != 220 || s.BindingState != Full {
		t.Fatalf("status=%+v", s)
	}
	q2 := NewTracker(p)
	if err := q2.Restore(2, 220); err != nil {
		t.Fatal(err)
	}
	if got := q2.Status(); got != s {
		t.Fatalf("restored=%+v want=%+v", got, s)
	}
}

func TestByteBoundaryRejectsBeforeCommit(t *testing.T) {
	p, _ := NewQualificationProfile(3, 200)
	q := NewTracker(p)
	if err := q.CheckAndCommit(150); err != nil {
		t.Fatal(err)
	}
	if err := q.Check(51); !errors.Is(err, ErrLogicalQuotaFull) {
		t.Fatalf("err=%v", err)
	}
	if got := q.Status(); got.CurrentCount != 1 || got.CurrentCommittedBytes != 150 {
		t.Fatalf("mutated on reject: %+v", got)
	}
}

func TestReleaseNumericsAreNeverDefaulted(t *testing.T) {
	if _, err := NewQualificationProfile(0, 100); !errors.Is(err, ErrInvalidProfile) {
		t.Fatalf("zero count err=%v", err)
	}
	if _, err := NewQualificationProfile(1, 0); !errors.Is(err, ErrInvalidProfile) {
		t.Fatalf("zero bytes err=%v", err)
	}
}

func TestWithCommitHoldsDecisionThroughPersistenceAndCountsOnlySuccess(t *testing.T) {
	p, _ := NewQualificationProfile(1, 100)
	q := NewTracker(p)
	persistErr := errors.New("persist failed")
	if err := q.WithCommit(50, func() error { return persistErr }); !errors.Is(err, persistErr) {
		t.Fatalf("persist err=%v", err)
	}
	if got := q.Status(); got.CurrentCount != 0 || got.CurrentCommittedBytes != 0 {
		t.Fatalf("failed persistence charged quota: %+v", got)
	}
	if err := q.WithCommit(50, func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	if got := q.Status(); got.CurrentCount != 1 || got.CurrentCommittedBytes != 50 || got.BindingState != Full {
		t.Fatalf("successful persistence status=%+v", got)
	}
}
