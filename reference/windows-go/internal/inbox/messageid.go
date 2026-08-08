package inbox

import (
	"crypto/rand"
	"errors"
	"fmt"
	"io"
)

var ErrMalformedMessageID = errors.New("malformed message id")

// NewMessageID returns the canonical lowercase RFC 9562 UUIDv4 text form.
// Message IDs are idempotency locators only; authorization remains bound to
// pair/trust generation and the complete durable envelope.
func NewMessageID() (string, error) { return newMessageID(rand.Reader) }

func newMessageID(r io.Reader) (string, error) {
	var b [16]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]), nil
}

func ValidateMessageID(id string) error {
	if len(id) != 36 || id[8] != '-' || id[13] != '-' || id[18] != '-' || id[23] != '-' {
		return ErrMalformedMessageID
	}
	for i := 0; i < len(id); i++ {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			continue
		}
		c := id[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return ErrMalformedMessageID
		}
	}
	if id[14] != '4' {
		return ErrMalformedMessageID
	}
	switch id[19] {
	case '8', '9', 'a', 'b':
	default:
		return ErrMalformedMessageID
	}
	return nil
}
