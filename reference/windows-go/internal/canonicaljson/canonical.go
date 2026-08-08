package canonicaljson

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"sort"
	"strings"
	"unicode/utf8"
)

const (
	CanonicalizationID  = "VERA_MESH_CANONICAL_JSON_V2"
	ContractSHA256      = "9a9e76d4d4ff8e8f258c8bc1eda1101a10ff467a9ce0ab0cf57f59fd9e71d0d4"
	FixtureCorpusSHA256 = "bc8d357b3efbb09db44f1dbaf9a6fa18057af85db6ec753e202c6909ea7f5465"

	ResourceProfileID     = "VERA_MESH_CANONICAL_RESOURCE_LIMITS_FIRST_SLICE_V1"
	ResourceProfileSHA256 = "9bf57f214b2da37dcfaf1c7a8e496496e74981a7efcde7b4ad78bf4b8a5a004b"
	MaxInputBytes         = 65536
	MaxNestingDepth       = 8
	MaxArrayElements      = 32
	MaxObjectMembers      = 32
	MaxStringUTF8Bytes    = 32768
)

var (
	ErrMalformedJSON                  = errors.New("MALFORMED_JSON")
	ErrInvalidUTF8                    = errors.New("INVALID_UTF8")
	ErrDuplicateObjectKey             = errors.New("DUPLICATE_OBJECT_KEY")
	ErrFloatForbidden                 = errors.New("FLOAT_FORBIDDEN")
	ErrInvalidUnicodeScalar           = errors.New("INVALID_UNICODE_SCALAR")
	ErrUnsupportedValueType           = errors.New("UNSUPPORTED_VALUE_TYPE")
	ErrCanonicalResourceLimitExceeded = errors.New("CANONICAL_RESOURCE_LIMIT_EXCEEDED")
)

type kind uint8

const (
	kindNull kind = iota
	kindBool
	kindString
	kindInteger
	kindArray
	kindObject
)

type member struct {
	key string
	val value
}

type value struct {
	kind kind
	b    bool
	s    string
	a    []value
	o    []member
}

// CanonicalizeSyntax implements the accepted VERA_MESH_CANONICAL_JSON_V2
// syntax/byte rules together with the accepted first-slice canonical resource
// profile. Target performance/conformance remains separately measured.
func CanonicalizeSyntax(input []byte) ([]byte, error) {
	if len(input) > MaxInputBytes {
		return nil, ErrCanonicalResourceLimitExceeded
	}
	if !utf8.Valid(input) {
		return nil, ErrInvalidUTF8
	}
	if len(input) >= 3 && input[0] == 0xef && input[1] == 0xbb && input[2] == 0xbf {
		return nil, ErrMalformedJSON
	}
	if err := validateRawJSONStrings(input); err != nil {
		return nil, err
	}

	dec := json.NewDecoder(bytes.NewReader(input))
	dec.UseNumber()
	v, err := parseValue(dec, 0)
	if err != nil {
		return nil, err
	}
	if tok, err := dec.Token(); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("%w: trailing token %v", ErrMalformedJSON, tok)
		}
		return nil, fmt.Errorf("%w: trailing data: %v", ErrMalformedJSON, err)
	}

	var out bytes.Buffer
	if err := emit(&out, v); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func parseValue(dec *json.Decoder, depth int) (value, error) {
	tok, err := dec.Token()
	if err != nil {
		return value{}, fmt.Errorf("%w: %v", ErrMalformedJSON, err)
	}
	switch t := tok.(type) {
	case nil:
		return value{kind: kindNull}, nil
	case bool:
		return value{kind: kindBool, b: t}, nil
	case string:
		if !scalarString(t) {
			return value{}, ErrInvalidUnicodeScalar
		}
		if len(t) > MaxStringUTF8Bytes {
			return value{}, ErrCanonicalResourceLimitExceeded
		}
		return value{kind: kindString, s: t}, nil
	case json.Number:
		raw := t.String()
		if strings.ContainsAny(raw, ".eE") {
			return value{}, ErrFloatForbidden
		}
		z := new(big.Int)
		if _, ok := z.SetString(raw, 10); !ok {
			return value{}, ErrMalformedJSON
		}
		return value{kind: kindInteger, s: z.String()}, nil
	case json.Delim:
		containerDepth := depth + 1
		if containerDepth > MaxNestingDepth {
			return value{}, ErrCanonicalResourceLimitExceeded
		}
		switch t {
		case '[':
			var vals []value
			count := 0
			for dec.More() {
				count++
				if count > MaxArrayElements {
					return value{}, ErrCanonicalResourceLimitExceeded
				}
				v, err := parseValue(dec, containerDepth)
				if err != nil {
					return value{}, err
				}
				vals = append(vals, v)
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim(']') {
				return value{}, ErrMalformedJSON
			}
			return value{kind: kindArray, a: vals}, nil
		case '{':
			seen := make(map[string]struct{})
			var members []member
			count := 0
			for dec.More() {
				keyTok, err := dec.Token()
				if err != nil {
					return value{}, fmt.Errorf("%w: object key: %v", ErrMalformedJSON, err)
				}
				key, ok := keyTok.(string)
				if !ok {
					return value{}, ErrMalformedJSON
				}
				if !scalarString(key) {
					return value{}, ErrInvalidUnicodeScalar
				}
				if len(key) > MaxStringUTF8Bytes {
					return value{}, ErrCanonicalResourceLimitExceeded
				}
				count++
				if count > MaxObjectMembers {
					return value{}, ErrCanonicalResourceLimitExceeded
				}
				if _, exists := seen[key]; exists {
					return value{}, ErrDuplicateObjectKey
				}
				seen[key] = struct{}{}
				val, err := parseValue(dec, containerDepth)
				if err != nil {
					return value{}, err
				}
				members = append(members, member{key: key, val: val})
			}
			end, err := dec.Token()
			if err != nil || end != json.Delim('}') {
				return value{}, ErrMalformedJSON
			}
			return value{kind: kindObject, o: members}, nil
		default:
			return value{}, ErrMalformedJSON
		}
	default:
		return value{}, ErrUnsupportedValueType
	}
}

func emit(out *bytes.Buffer, v value) error {
	switch v.kind {
	case kindNull:
		out.WriteString("null")
	case kindBool:
		if v.b {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case kindString:
		emitString(out, v.s)
	case kindInteger:
		out.WriteString(v.s)
	case kindArray:
		out.WriteByte('[')
		for i := range v.a {
			if i > 0 {
				out.WriteByte(',')
			}
			if err := emit(out, v.a[i]); err != nil {
				return err
			}
		}
		out.WriteByte(']')
	case kindObject:
		members := append([]member(nil), v.o...)
		sort.Slice(members, func(i, j int) bool { return members[i].key < members[j].key })
		out.WriteByte('{')
		for i := range members {
			if i > 0 {
				out.WriteByte(',')
			}
			emitString(out, members[i].key)
			out.WriteByte(':')
			if err := emit(out, members[i].val); err != nil {
				return err
			}
		}
		out.WriteByte('}')
	default:
		return ErrUnsupportedValueType
	}
	return nil
}

func emitString(out *bytes.Buffer, s string) {
	const hexDigits = "0123456789abcdef"
	out.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\b':
			out.WriteString(`\b`)
		case '\t':
			out.WriteString(`\t`)
		case '\n':
			out.WriteString(`\n`)
		case '\f':
			out.WriteString(`\f`)
		case '\r':
			out.WriteString(`\r`)
		default:
			if r >= 0 && r <= 0x1f {
				out.WriteString(`\u00`)
				out.WriteByte(hexDigits[(r>>4)&0xf])
				out.WriteByte(hexDigits[r&0xf])
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
}

func scalarString(s string) bool {
	if !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if r >= 0xd800 && r <= 0xdfff {
			return false
		}
	}
	return true
}

func validateRawJSONStrings(input []byte) error {
	for i := 0; i < len(input); i++ {
		if input[i] != '"' {
			continue
		}
		decodedBytes := 0
		i++
		for ; i < len(input); i++ {
			c := input[i]
			if c == '"' {
				break
			}
			if c < 0x20 {
				return ErrMalformedJSON
			}
			if c != '\\' {
				if c < utf8.RuneSelf {
					decodedBytes++
				} else {
					_, size := utf8.DecodeRune(input[i:])
					if size <= 0 {
						return ErrInvalidUTF8
					}
					decodedBytes += size
					i += size - 1
				}
				if decodedBytes > MaxStringUTF8Bytes {
					return ErrCanonicalResourceLimitExceeded
				}
				continue
			}
			i++
			if i >= len(input) {
				return ErrMalformedJSON
			}
			switch input[i] {
			case '"', '\\', '/', 'b', 'f', 'n', 'r', 't':
				decodedBytes++
			case 'u':
				cu, next, ok := parseHex4(input, i+1)
				if !ok {
					return ErrMalformedJSON
				}
				i = next - 1
				if cu >= 0xd800 && cu <= 0xdbff {
					if i+6 >= len(input) || input[i+1] != '\\' || input[i+2] != 'u' {
						return ErrInvalidUnicodeScalar
					}
					low, afterLow, ok := parseHex4(input, i+3)
					if !ok {
						return ErrMalformedJSON
					}
					if low < 0xdc00 || low > 0xdfff {
						return ErrInvalidUnicodeScalar
					}
					r := rune(0x10000 + (uint32(cu)-0xd800)<<10 + (uint32(low) - 0xdc00))
					decodedBytes += utf8.RuneLen(r)
					i = afterLow - 1
				} else if cu >= 0xdc00 && cu <= 0xdfff {
					return ErrInvalidUnicodeScalar
				} else {
					decodedBytes += utf8.RuneLen(rune(cu))
				}
			default:
				return ErrMalformedJSON
			}
			if decodedBytes > MaxStringUTF8Bytes {
				return ErrCanonicalResourceLimitExceeded
			}
		}
		if i >= len(input) || input[i] != '"' {
			return ErrMalformedJSON
		}
	}
	return nil
}

func parseHex4(input []byte, start int) (uint16, int, bool) {
	if start+4 > len(input) {
		return 0, start, false
	}
	var v uint16
	for i := start; i < start+4; i++ {
		d, ok := fromHex(input[i])
		if !ok {
			return 0, start, false
		}
		v = v<<4 | uint16(d)
	}
	return v, start + 4, true
}

func fromHex(c byte) (byte, bool) {
	switch {
	case c >= '0' && c <= '9':
		return c - '0', true
	case c >= 'a' && c <= 'f':
		return c - 'a' + 10, true
	case c >= 'A' && c <= 'F':
		return c - 'A' + 10, true
	default:
		return 0, false
	}
}
