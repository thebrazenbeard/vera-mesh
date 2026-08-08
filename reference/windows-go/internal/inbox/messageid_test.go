package inbox

import (
	"bytes"
	"errors"
	"testing"
)

func TestMessageIDV4ExactFixture(t *testing.T) {
	id, err := newMessageID(bytes.NewReader([]byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}))
	if err != nil {
		t.Fatal(err)
	}
	const want = "00010203-0405-4607-8809-0a0b0c0d0e0f"
	if id != want {
		t.Fatalf("id=%q want=%q", id, want)
	}
	if err := ValidateMessageID(id); err != nil {
		t.Fatalf("ValidateMessageID: %v", err)
	}
}

func TestMessageIDRejectsNonCanonicalOrWrongVersion(t *testing.T) {
	bad := []string{
		"00010203-0405-7607-8809-0a0b0c0d0e0f",
		"00010203-0405-4607-7809-0a0b0c0d0e0f",
		"00010203-0405-4607-8809-0A0B0C0D0E0F",
		"000102030405460788090a0b0c0d0e0f",
	}
	for _, id := range bad {
		if err := ValidateMessageID(id); !errors.Is(err, ErrMalformedMessageID) {
			t.Fatalf("ValidateMessageID(%q)=%v want ErrMalformedMessageID", id, err)
		}
	}
}
