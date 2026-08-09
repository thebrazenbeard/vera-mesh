package canonicaljson

import (
	"errors"
	"testing"
)

func TestRequiredNumberDigitInvalidUTF8Precedence(t *testing.T) {
	tests := []struct {
		name  string
		input []byte
	}{
		{"after-minus", []byte{'-', 0xff}},
		{"after-decimal-point", []byte{'1', '.', 0xff}},
		{"after-exponent-marker", []byte{'1', 'e', 0xff}},
		{"after-exponent-plus", []byte{'1', 'e', '+', 0xff}},
		{"after-exponent-minus", []byte{'1', 'e', '-', 0xff}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := CanonicalizeSyntax(tc.input)
			if len(got) != 0 {
				t.Fatalf("failure emitted partial canonical bytes: %x", got)
			}
			if !errors.Is(err, ErrInvalidUTF8) {
				t.Fatalf("err=%v want ErrInvalidUTF8", err)
			}
		})
	}
}

func TestRequiredNumberDigitASCIINonDigitStaysMalformed(t *testing.T) {
	for _, input := range [][]byte{
		[]byte("-x"),
		[]byte("1.x"),
		[]byte("1ex"),
		[]byte("1e+x"),
		[]byte("1e-x"),
	} {
		if _, err := CanonicalizeSyntax(input); !errors.Is(err, ErrMalformedJSON) {
			t.Fatalf("input=%q err=%v want ErrMalformedJSON", input, err)
		}
	}
}

func TestRequiredNumberDigitEOFStaysMalformed(t *testing.T) {
	for _, input := range [][]byte{
		[]byte("-"),
		[]byte("1."),
		[]byte("1e"),
		[]byte("1e+"),
		[]byte("1e-"),
	} {
		if _, err := CanonicalizeSyntax(input); !errors.Is(err, ErrMalformedJSON) {
			t.Fatalf("input=%q err=%v want ErrMalformedJSON", input, err)
		}
	}
}

func TestValidFloatFormsRemainFloatForbidden(t *testing.T) {
	for _, input := range [][]byte{
		[]byte("-1.0"),
		[]byte("1.0"),
		[]byte("1e1"),
		[]byte("1e+1"),
		[]byte("1e-1"),
	} {
		if _, err := CanonicalizeSyntax(input); !errors.Is(err, ErrFloatForbidden) {
			t.Fatalf("input=%q err=%v want ErrFloatForbidden", input, err)
		}
	}
}

func TestStringEscapeRequiredASCIIInvalidUTF8Precedence(t *testing.T) {
	tests := []struct {
		name  string
		input []byte
	}{
		{"escape-selector", []byte{'"', '\\', 0xff, '"'}},
		{"unicode-hex-1", []byte{'"', '\\', 'u', 0xff, '0', '0', '0', '"'}},
		{"unicode-hex-2", []byte{'"', '\\', 'u', '0', 0xff, '0', '0', '"'}},
		{"unicode-hex-3", []byte{'"', '\\', 'u', '0', '0', 0xff, '0', '"'}},
		{"unicode-hex-4", []byte{'"', '\\', 'u', '0', '0', '0', 0xff, '"'}},
		{"unicode-hex-short-1", []byte{'"', '\\', 'u', 0xff}},
		{"unicode-hex-short-2", []byte{'"', '\\', 'u', '0', 0xff}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := CanonicalizeSyntax(tc.input)
			if len(got) != 0 {
				t.Fatalf("failure emitted partial canonical bytes: %x", got)
			}
			if !errors.Is(err, ErrInvalidUTF8) {
				t.Fatalf("err=%v want ErrInvalidUTF8", err)
			}
		})
	}
}

func TestStringEscapeASCIIGrammarErrorsStayMalformed(t *testing.T) {
	for _, input := range [][]byte{
		[]byte(`"\q"`),
		[]byte(`"\u12g4"`),
		[]byte(`"\u12"`),
	} {
		if _, err := CanonicalizeSyntax(input); !errors.Is(err, ErrMalformedJSON) {
			t.Fatalf("input=%q err=%v want ErrMalformedJSON", input, err)
		}
	}
}
