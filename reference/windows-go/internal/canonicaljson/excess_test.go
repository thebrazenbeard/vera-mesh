package canonicaljson

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

func arrayPrefix32() string {
	return "[" + strings.TrimSuffix(strings.Repeat("0,", MaxArrayElements), ",")
}

func objectPrefix32() string {
	parts := make([]string, 0, MaxObjectMembers)
	for i := 0; i < MaxObjectMembers; i++ {
		parts = append(parts, fmt.Sprintf("%q:%d", fmt.Sprintf("k%02d", i), i))
	}
	return "{" + strings.Join(parts, ",")
}

func requireTypedFailureNoOutput(t *testing.T, input []byte, want error) {
	t.Helper()
	got, err := CanonicalizeSyntax(input)
	if len(got) != 0 {
		t.Fatalf("failure emitted partial output: %x", got)
	}
	if !errors.Is(err, want) {
		t.Fatalf("err=%v want=%v", err, want)
	}
}

func TestArrayElement33ValidatesExcessValueBeforeResource(t *testing.T) {
	prefix := arrayPrefix32() + ","
	requireTypedFailureNoOutput(t, []byte(prefix+`"unterminated]`), ErrMalformedJSON)
	requireTypedFailureNoOutput(t, []byte(prefix+`truX]`), ErrMalformedJSON)
	requireTypedFailureNoOutput(t, []byte(prefix+`1e]`), ErrMalformedJSON)

	invalidUTF8 := append([]byte(prefix+`"`), 0xff)
	invalidUTF8 = append(invalidUTF8, []byte(`"]`)...)
	requireTypedFailureNoOutput(t, invalidUTF8, ErrInvalidUTF8)
	requireTypedFailureNoOutput(t, []byte(prefix+`"\ud800"]`), ErrInvalidUnicodeScalar)
	requireTypedFailureNoOutput(t, []byte(prefix+`0]`), ErrCanonicalResourceLimitExceeded)
	requireTypedFailureNoOutput(t, []byte(prefix+`{"x":[1]}]`), ErrCanonicalResourceLimitExceeded)
}

func TestObjectMember33ValidatesWholeExcessMemberBeforeResource(t *testing.T) {
	prefix := objectPrefix32() + ","
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32"}`), ErrMalformedJSON)
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32":"unterminated}`), ErrMalformedJSON)
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32":truX}`), ErrMalformedJSON)

	invalidUTF8 := append([]byte(prefix+`"k32":"`), 0xff)
	invalidUTF8 = append(invalidUTF8, []byte(`"}`)...)
	requireTypedFailureNoOutput(t, invalidUTF8, ErrInvalidUTF8)
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32":"\ud800"}`), ErrInvalidUnicodeScalar)
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32":0}`), ErrCanonicalResourceLimitExceeded)
	requireTypedFailureNoOutput(t, []byte(prefix+`"k32":{"x":[1]}}`), ErrCanonicalResourceLimitExceeded)
}
