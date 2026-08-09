package quota

import (
	"errors"
	"sync"
)

var (
	ErrInvalidProfile   = errors.New("invalid quota profile")
	ErrLogicalQuotaFull = errors.New("logical quota full")
	ErrInvalidRestore   = errors.New("invalid quota restore")
)

type BindingState string

const (
	Available BindingState = "AVAILABLE"
	Full      BindingState = "FULL"
)

type Profile struct {
	MaxCount uint64
	MaxBytes uint64
}

// NewQualificationProfile constructs an explicitly bounded qualification profile.
// Release defaults are intentionally not provided: release quota numerics remain
// unbound until product usage and target measurement establish them.
func NewQualificationProfile(maxCount, maxBytes uint64) (Profile, error) {
	if maxCount == 0 || maxBytes == 0 {
		return Profile{}, ErrInvalidProfile
	}
	return Profile{MaxCount: maxCount, MaxBytes: maxBytes}, nil
}

type Status struct {
	CurrentCount          uint64
	MaxCount              uint64
	CurrentCommittedBytes uint64
	MaxBytes              uint64
	BindingState          BindingState
}

type Tracker struct {
	mu             sync.Mutex
	profile        Profile
	count          uint64
	committedBytes uint64
}

func NewTracker(profile Profile) *Tracker {
	return &Tracker{profile: profile}
}

func (t *Tracker) Check(frameBytes uint64) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.checkLocked(frameBytes)
}

func (t *Tracker) checkLocked(frameBytes uint64) error {
	if frameBytes == 0 {
		return ErrInvalidProfile
	}
	if t.count >= t.profile.MaxCount {
		return ErrLogicalQuotaFull
	}
	if frameBytes > t.profile.MaxBytes-t.committedBytes {
		return ErrLogicalQuotaFull
	}
	return nil
}

func (t *Tracker) Commit(frameBytes uint64) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if err := t.checkLocked(frameBytes); err != nil {
		return err
	}
	t.count++
	t.committedBytes += frameBytes
	return nil
}

func (t *Tracker) CheckAndCommit(frameBytes uint64) error {
	return t.Commit(frameBytes)
}

// WithCommit holds the quota decision stable across the durable persistence
// operation and advances counters only after that operation succeeds.
func (t *Tracker) WithCommit(frameBytes uint64, persist func() error) error {
	if persist == nil {
		return ErrInvalidProfile
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if err := t.checkLocked(frameBytes); err != nil {
		return err
	}
	if err := persist(); err != nil {
		return err
	}
	t.count++
	t.committedBytes += frameBytes
	return nil
}

func (t *Tracker) Restore(count, committedBytes uint64) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if count > t.profile.MaxCount || committedBytes > t.profile.MaxBytes {
		return ErrInvalidRestore
	}
	if count == 0 && committedBytes != 0 {
		return ErrInvalidRestore
	}
	t.count = count
	t.committedBytes = committedBytes
	return nil
}

func (t *Tracker) Status() Status {
	t.mu.Lock()
	defer t.mu.Unlock()
	state := Available
	if t.count >= t.profile.MaxCount || t.committedBytes >= t.profile.MaxBytes {
		state = Full
	}
	return Status{
		CurrentCount:          t.count,
		MaxCount:              t.profile.MaxCount,
		CurrentCommittedBytes: t.committedBytes,
		MaxBytes:              t.profile.MaxBytes,
		BindingState:          state,
	}
}
