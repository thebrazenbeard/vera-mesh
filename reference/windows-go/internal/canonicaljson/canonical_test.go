package canonicaljson

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"testing"
)

type positiveFixture struct {
	name, inputHex, wantHex, wantSHA string
	wantLen                          int
}

func TestAcceptedFixtureCorpusExactBytesAndDigests(t *testing.T) {
	fixtures := []positiveFixture{
		{"CJV2-001-ASCII-ORDER", "7b227a223a22782079222c2261223a317d", "7b2261223a312c227a223a22782079227d", "630b686346c4f65cef2c20f3b38452c3feb48c4e03a19e7e7004f5aa4c70f3cd", 17},
		{"CJV2-002-HTML-CHARS", "7b2278223a223c3e26227d", "7b2278223a223c3e26227d", "807c07d6f1cb39b4c87e34312ece8b87016fdfab365a17ec0687f81fc8cc029e", 11},
		{"CJV2-003-U2028-U2029", "7b2278223a225c75323032385c7532303239227d", "7b2278223a22e280a8e280a9227d", "dfd90bc01a3641c8b6b5cc1b1161de1f91d63ef197c1184b501ee3f4e5d7549d", 14},
		{"CJV2-004-ASTRAL-SURROGATE-PAIR-INPUT", "7b2278223a225c75643833645c7564653030227d", "7b2278223a22f09f9880227d", "c10cb8a0c573e0eafbed00d33a65f0a83da0cb03445908aed65f7deecc63019e", 12},
		{"CJV2-005-CONTROLS", "7b2278223a225c75303030305c625c745c6e5c665c725c7530303166227d", "7b2278223a225c75303030305c625c745c6e5c665c725c7530303166227d", "c553de3e2c67b87c3da40bcd0ac7a191b7ad83aa7b069b8cfe647a0c6ac8aa9a", 30},
		{"CJV2-006-SCALAR-KEY-ORDER", "7b225c75643833645c7564653030223a2261737472616c222c225c7565303030223a22626d70227d", "7b22ee8080223a22626d70222c22f09f9880223a2261737472616c227d", "4874d36535a1b5dfdad7f2b3af83f87debd2f0795ae7d2d09447e17223c42dd0", 29},
		{"CJV2-007-NO-NORMALIZATION", "7b2279223a2265cc81222c2278223a22c3a9227d", "7b2278223a22c3a9222c2279223a2265cc81227d", "60916069ce11e17c78d8ace441b499ffde5fd3aeb5c441fe8e886f17cd980925", 20},
		{"CJV2-008-BIG-INTEGER", "7b226e223a3132333435363738393031323334353637383930313233343536373839302c226d223a2d3132333435363738393031323334353637383930313233343536373839307d", "7b226d223a2d3132333435363738393031323334353637383930313233343536373839302c226e223a3132333435363738393031323334353637383930313233343536373839307d", "1385303b348444fcc004e2ae9c60afa2e4ed29a28d6db76ebb939c024ab340ba", 72},
		{"CJV2-009-ARRAY-ORDER", "7b2278223a5b332c322c312c22c3a9222c22f09f9880222c747275652c66616c73652c6e756c6c5d7d", "7b2278223a5b332c322c312c22c3a9222c22f09f9880222c747275652c66616c73652c6e756c6c5d7d", "5838ff729a33c9b065ec2962d6db23a38a66c7e3f7171f21167e5466e393e2f2", 41},
		{"CJV2-010-ESCAPE-QUOTE-BACKSLASH-SLASH", "7b2278223a225c225c5c2f227d", "7b2278223a225c225c5c2f227d", "92e28b2a4be5895370b79551467fc8702f98478fa315da4c140049e98bcfbb11", 13},
		{"CJV2-011-NO-NORMALIZATION-DISTINCT-KEYS", "7b22c3a9223a312c2265cc81223a327d", "7b2265cc81223a322c22c3a9223a317d", "a7962fb10dc1255be368ece9c22b2256605921dc6d0a8c9409d3ee406bcb86e5", 16},
	}
	for _, tc := range fixtures {
		t.Run(tc.name, func(t *testing.T) {
			input, err := hex.DecodeString(tc.inputHex)
			if err != nil {
				t.Fatal(err)
			}
			got, err := CanonicalizeSyntax(input)
			if err != nil {
				t.Fatalf("CanonicalizeSyntax: %v", err)
			}
			if hex.EncodeToString(got) != tc.wantHex {
				t.Fatalf("bytes=%x want=%s", got, tc.wantHex)
			}
			if len(got) != tc.wantLen {
				t.Fatalf("len=%d want=%d", len(got), tc.wantLen)
			}
			sum := sha256.Sum256(got)
			if hex.EncodeToString(sum[:]) != tc.wantSHA {
				t.Fatalf("sha=%x want=%s", sum, tc.wantSHA)
			}
		})
	}
}

func TestAcceptedHostileCorpusTypedFailures(t *testing.T) {
	fixtures := []struct {
		name, inputHex string
		want           error
	}{
		{"CJV2-H01-DUPLICATE-KEY", "7b2261223a312c2261223a327d", ErrDuplicateObjectKey},
		{"CJV2-H02-FLOAT-DECIMAL", "7b2278223a312e307d", ErrFloatForbidden},
		{"CJV2-H03-FLOAT-EXP", "7b2278223a31652d367d", ErrFloatForbidden},
		{"CJV2-H04-NEGZERO-FLOAT", "7b2278223a2d302e307d", ErrFloatForbidden},
		{"CJV2-H05-NAN", "7b2278223a4e614e7d", ErrMalformedJSON},
		{"CJV2-H06-UNPAIRED-HIGH-SURROGATE-VALUE", "7b2278223a225c7564383030227d", ErrInvalidUnicodeScalar},
		{"CJV2-H07-UNPAIRED-LOW-SURROGATE-KEY", "7b225c7564633030223a317d", ErrInvalidUnicodeScalar},
		{"CJV2-H08-INVALID-UTF8", "7b2278223a22ff227d", ErrInvalidUTF8},
		{"CJV2-H09-TRAILING-GARBAGE", "7b2278223a317d78", ErrMalformedJSON},
		{"CJV2-H10-ESCAPED-EQUIVALENT-DUPLICATE-ASCII", "7b2261223a312c225c7530303631223a327d", ErrDuplicateObjectKey},
		{"CJV2-H11-ESCAPED-EQUIVALENT-DUPLICATE-ASTRAL", "7b22f09f9880223a312c225c75643833645c7564653030223a327d", ErrDuplicateObjectKey},
	}
	for _, tc := range fixtures {
		t.Run(tc.name, func(t *testing.T) {
			input, err := hex.DecodeString(tc.inputHex)
			if err != nil {
				t.Fatal(err)
			}
			got, err := CanonicalizeSyntax(input)
			if len(got) != 0 {
				t.Fatalf("failure emitted partial canonical bytes: %x", got)
			}
			if !errors.Is(err, tc.want) {
				t.Fatalf("err=%v want=%v", err, tc.want)
			}
		})
	}
}

func TestResourceProfileRawInputBoundaryAndPrecedence(t *testing.T) {
	exact := append([]byte(`{"x":1}`), bytes.Repeat([]byte{' '}, MaxInputBytes-len(`{"x":1}`))...)
	if len(exact) != MaxInputBytes {
		t.Fatalf("fixture len=%d want=%d", len(exact), MaxInputBytes)
	}
	if _, err := CanonicalizeSyntax(exact); err != nil {
		t.Fatalf("exact max input rejected: %v", err)
	}
	over := append(append([]byte(nil), exact...), ' ')
	if _, err := CanonicalizeSyntax(over); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("over max input err=%v want ErrCanonicalResourceLimitExceeded", err)
	}
	invalidOver := bytes.Repeat([]byte{' '}, MaxInputBytes+1)
	invalidOver[0] = 0xff
	if _, err := CanonicalizeSyntax(invalidOver); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("over-limit invalid UTF8 err=%v want resource-limit precedence", err)
	}
	invalidWithin := []byte{0xff}
	if _, err := CanonicalizeSyntax(invalidWithin); !errors.Is(err, ErrInvalidUTF8) {
		t.Fatalf("within-limit invalid UTF8 err=%v want ErrInvalidUTF8", err)
	}
}

func TestResourceProfileNestingBoundary(t *testing.T) {
	depth8 := []byte(strings.Repeat("[", MaxNestingDepth) + "0" + strings.Repeat("]", MaxNestingDepth))
	if _, err := CanonicalizeSyntax(depth8); err != nil {
		t.Fatalf("depth8 rejected: %v", err)
	}
	depth9 := []byte(strings.Repeat("[", MaxNestingDepth+1) + "0" + strings.Repeat("]", MaxNestingDepth+1))
	if _, err := CanonicalizeSyntax(depth9); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("depth9 err=%v want ErrCanonicalResourceLimitExceeded", err)
	}
}

func TestResourceProfileObjectAndArrayBoundaries(t *testing.T) {
	object := func(n int) []byte {
		parts := make([]string, 0, n)
		for i := 0; i < n; i++ {
			parts = append(parts, fmt.Sprintf("%q:%d", fmt.Sprintf("k%02d", i), i))
		}
		return []byte("{" + strings.Join(parts, ",") + "}")
	}
	if _, err := CanonicalizeSyntax(object(MaxObjectMembers)); err != nil {
		t.Fatalf("32-member object rejected: %v", err)
	}
	if _, err := CanonicalizeSyntax(object(MaxObjectMembers + 1)); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("33-member object err=%v want resource limit", err)
	}

	array := func(n int) []byte {
		parts := make([]string, n)
		for i := range parts {
			parts[i] = "0"
		}
		return []byte("[" + strings.Join(parts, ",") + "]")
	}
	if _, err := CanonicalizeSyntax(array(MaxArrayElements)); err != nil {
		t.Fatalf("32-element array rejected: %v", err)
	}
	if _, err := CanonicalizeSyntax(array(MaxArrayElements + 1)); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("33-element array err=%v want resource limit", err)
	}
}

func TestResourceProfileDecodedStringUTF8Boundary(t *testing.T) {
	asciiExact := []byte(`{"x":"` + strings.Repeat("a", MaxStringUTF8Bytes) + `"}`)
	if _, err := CanonicalizeSyntax(asciiExact); err != nil {
		t.Fatalf("exact ASCII string rejected: %v", err)
	}
	asciiOver := []byte(`{"x":"` + strings.Repeat("a", MaxStringUTF8Bytes+1) + `"}`)
	if _, err := CanonicalizeSyntax(asciiOver); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("over ASCII string err=%v want resource limit", err)
	}

	keyOver := []byte(`{"` + strings.Repeat("k", MaxStringUTF8Bytes+1) + `":0}`)
	if _, err := CanonicalizeSyntax(keyOver); !errors.Is(err, ErrCanonicalResourceLimitExceeded) {
		t.Fatalf("over key string err=%v want resource limit", err)
	}

	multiExact := []byte(`{"x":"` + strings.Repeat("😀", MaxStringUTF8Bytes/4) + `"}`)
	if _, err := CanonicalizeSyntax(multiExact); err != nil {
		t.Fatalf("exact multibyte string rejected: %v", err)
	}

	escaped := []byte(`{"x":"` + strings.Repeat(`\u0061`, 6000) + `"}`)
	if len(escaped) <= MaxStringUTF8Bytes || len(escaped) > MaxInputBytes {
		t.Fatalf("escaped source fixture length=%d outside intended range", len(escaped))
	}
	if _, err := CanonicalizeSyntax(escaped); err != nil {
		t.Fatalf("escaped long-source short-decoded string rejected: %v", err)
	}
}

func TestResourceProfileDuplicatePrecedesMemberLimitWithinBoundary(t *testing.T) {
	parts := []string{`"k00":0`, `"a":1`}
	for i := 2; i < MaxObjectMembers-1; i++ {
		parts = append(parts, fmt.Sprintf("%q:%d", fmt.Sprintf("k%02d", i), i))
	}
	parts = append(parts, `"\u0061":31`)
	input := []byte("{" + strings.Join(parts, ",") + "}")
	if _, err := CanonicalizeSyntax(input); !errors.Is(err, ErrDuplicateObjectKey) {
		t.Fatalf("decoded duplicate at member32 err=%v want ErrDuplicateObjectKey", err)
	}
}

func TestResourceProfileIdentityIsFrozen(t *testing.T) {
	if ResourceProfileID != "VERA_MESH_CANONICAL_RESOURCE_LIMITS_FIRST_SLICE_V1" {
		t.Fatalf("ResourceProfileID=%q", ResourceProfileID)
	}
	if ResourceProfileSHA256 != "9bf57f214b2da37dcfaf1c7a8e496496e74981a7efcde7b4ad78bf4b8a5a004b" {
		t.Fatalf("ResourceProfileSHA256=%q", ResourceProfileSHA256)
	}
}
