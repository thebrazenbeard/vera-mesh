package canonicaljson

import (
	"bytes"
	"errors"
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

type parser struct {
	input []byte
	pos   int
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

	p := parser{input: input}
	p.skipWhitespace()
	v, err := p.parseValue(0)
	if err != nil {
		return nil, err
	}
	p.skipWhitespace()
	if p.pos != len(input) {
		return nil, ErrMalformedJSON
	}

	var out bytes.Buffer
	if err := emit(&out, v); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func (p *parser) parseValue(depth int) (value, error) {
	p.skipWhitespace()
	if p.pos >= len(p.input) {
		return value{}, ErrMalformedJSON
	}

	switch c := p.input[p.pos]; {
	case c == 'n':
		if !p.consumeLiteral("null") {
			return value{}, ErrMalformedJSON
		}
		return value{kind: kindNull}, nil
	case c == 't':
		if !p.consumeLiteral("true") {
			return value{}, ErrMalformedJSON
		}
		return value{kind: kindBool, b: true}, nil
	case c == 'f':
		if !p.consumeLiteral("false") {
			return value{}, ErrMalformedJSON
		}
		return value{kind: kindBool, b: false}, nil
	case c == '"':
		s, err := p.parseString()
		if err != nil {
			return value{}, err
		}
		return value{kind: kindString, s: s}, nil
	case c == '[':
		return p.parseArray(depth)
	case c == '{':
		return p.parseObject(depth)
	case c == '-' || (c >= '0' && c <= '9'):
		return p.parseNumber()
	default:
		return value{}, ErrMalformedJSON
	}
}

func (p *parser) parseArray(depth int) (value, error) {
	containerDepth := depth + 1
	if containerDepth > MaxNestingDepth {
		return value{}, ErrCanonicalResourceLimitExceeded
	}
	p.pos++
	p.skipWhitespace()
	if p.take(']') {
		return value{kind: kindArray}, nil
	}

	vals := make([]value, 0, min(MaxArrayElements, 4))
	for {
		if len(vals) >= MaxArrayElements {
			p.skipWhitespace()
			if p.pos >= len(p.input) || !isValueStart(p.input[p.pos]) {
				return value{}, ErrMalformedJSON
			}
			return value{}, ErrCanonicalResourceLimitExceeded
		}
		v, err := p.parseValue(containerDepth)
		if err != nil {
			return value{}, err
		}
		vals = append(vals, v)
		p.skipWhitespace()
		if p.take(']') {
			return value{kind: kindArray, a: vals}, nil
		}
		if !p.take(',') {
			return value{}, ErrMalformedJSON
		}
		p.skipWhitespace()
		if p.pos >= len(p.input) || p.input[p.pos] == ']' {
			return value{}, ErrMalformedJSON
		}
	}
}

func (p *parser) parseObject(depth int) (value, error) {
	containerDepth := depth + 1
	if containerDepth > MaxNestingDepth {
		return value{}, ErrCanonicalResourceLimitExceeded
	}
	p.pos++
	p.skipWhitespace()
	if p.take('}') {
		return value{kind: kindObject}, nil
	}

	seen := make(map[string]struct{}, min(MaxObjectMembers, 4))
	members := make([]member, 0, min(MaxObjectMembers, 4))
	for {
		p.skipWhitespace()
		if p.pos >= len(p.input) || p.input[p.pos] != '"' {
			return value{}, ErrMalformedJSON
		}
		key, err := p.parseString()
		if err != nil {
			return value{}, err
		}
		if len(members) >= MaxObjectMembers {
			return value{}, ErrCanonicalResourceLimitExceeded
		}
		if _, exists := seen[key]; exists {
			return value{}, ErrDuplicateObjectKey
		}
		seen[key] = struct{}{}
		p.skipWhitespace()
		if !p.take(':') {
			return value{}, ErrMalformedJSON
		}
		val, err := p.parseValue(containerDepth)
		if err != nil {
			return value{}, err
		}
		members = append(members, member{key: key, val: val})
		p.skipWhitespace()
		if p.take('}') {
			return value{kind: kindObject, o: members}, nil
		}
		if !p.take(',') {
			return value{}, ErrMalformedJSON
		}
		p.skipWhitespace()
		if p.pos >= len(p.input) || p.input[p.pos] == '}' {
			return value{}, ErrMalformedJSON
		}
	}
}

func (p *parser) parseNumber() (value, error) {
	start := p.pos
	if p.take('-') {
		if p.pos >= len(p.input) {
			return value{}, ErrMalformedJSON
		}
	}

	if p.pos >= len(p.input) {
		return value{}, ErrMalformedJSON
	}
	if p.input[p.pos] == '0' {
		p.pos++
		if p.pos < len(p.input) && p.input[p.pos] >= '0' && p.input[p.pos] <= '9' {
			return value{}, ErrMalformedJSON
		}
	} else if p.input[p.pos] >= '1' && p.input[p.pos] <= '9' {
		for p.pos < len(p.input) && p.input[p.pos] >= '0' && p.input[p.pos] <= '9' {
			p.pos++
		}
	} else {
		return value{}, ErrMalformedJSON
	}

	isFloat := false
	if p.pos < len(p.input) && p.input[p.pos] == '.' {
		isFloat = true
		p.pos++
		if p.pos >= len(p.input) || p.input[p.pos] < '0' || p.input[p.pos] > '9' {
			return value{}, ErrMalformedJSON
		}
		for p.pos < len(p.input) && p.input[p.pos] >= '0' && p.input[p.pos] <= '9' {
			p.pos++
		}
	}
	if p.pos < len(p.input) && (p.input[p.pos] == 'e' || p.input[p.pos] == 'E') {
		isFloat = true
		p.pos++
		if p.pos < len(p.input) && (p.input[p.pos] == '+' || p.input[p.pos] == '-') {
			p.pos++
		}
		if p.pos >= len(p.input) || p.input[p.pos] < '0' || p.input[p.pos] > '9' {
			return value{}, ErrMalformedJSON
		}
		for p.pos < len(p.input) && p.input[p.pos] >= '0' && p.input[p.pos] <= '9' {
			p.pos++
		}
	}
	if p.pos < len(p.input) && !isValueTerminator(p.input[p.pos]) {
		return value{}, ErrMalformedJSON
	}
	if isFloat {
		return value{}, ErrFloatForbidden
	}

	raw := string(p.input[start:p.pos])
	z := new(big.Int)
	if _, ok := z.SetString(raw, 10); !ok {
		return value{}, ErrMalformedJSON
	}
	return value{kind: kindInteger, s: z.String()}, nil
}

func (p *parser) parseString() (string, error) {
	if !p.take('"') {
		return "", ErrMalformedJSON
	}
	var out strings.Builder
	decodedBytes := 0
	for p.pos < len(p.input) {
		c := p.input[p.pos]
		if c == '"' {
			p.pos++
			return out.String(), nil
		}
		if c < 0x20 {
			return "", ErrMalformedJSON
		}
		if c != '\\' {
			r, size := utf8.DecodeRune(p.input[p.pos:])
			if r == utf8.RuneError && size == 1 {
				return "", ErrInvalidUTF8
			}
			if r >= 0xd800 && r <= 0xdfff {
				return "", ErrInvalidUnicodeScalar
			}
			decodedBytes += size
			if decodedBytes > MaxStringUTF8Bytes {
				return "", ErrCanonicalResourceLimitExceeded
			}
			out.Write(p.input[p.pos : p.pos+size])
			p.pos += size
			continue
		}

		p.pos++
		if p.pos >= len(p.input) {
			return "", ErrMalformedJSON
		}
		switch esc := p.input[p.pos]; esc {
		case '"', '\\', '/':
			decodedBytes++
			if decodedBytes > MaxStringUTF8Bytes {
				return "", ErrCanonicalResourceLimitExceeded
			}
			out.WriteByte(esc)
			p.pos++
		case 'b', 'f', 'n', 'r', 't':
			decodedBytes++
			if decodedBytes > MaxStringUTF8Bytes {
				return "", ErrCanonicalResourceLimitExceeded
			}
			switch esc {
			case 'b':
				out.WriteByte('\b')
			case 'f':
				out.WriteByte('\f')
			case 'n':
				out.WriteByte('\n')
			case 'r':
				out.WriteByte('\r')
			case 't':
				out.WriteByte('\t')
			}
			p.pos++
		case 'u':
			cu, next, ok := parseHex4(p.input, p.pos+1)
			if !ok {
				return "", ErrMalformedJSON
			}
			p.pos = next
			var r rune
			if cu >= 0xd800 && cu <= 0xdbff {
				if p.pos+6 > len(p.input) || p.input[p.pos] != '\\' || p.input[p.pos+1] != 'u' {
					return "", ErrInvalidUnicodeScalar
				}
				low, afterLow, ok := parseHex4(p.input, p.pos+2)
				if !ok {
					return "", ErrMalformedJSON
				}
				if low < 0xdc00 || low > 0xdfff {
					return "", ErrInvalidUnicodeScalar
				}
				r = rune(0x10000 + (uint32(cu)-0xd800)<<10 + (uint32(low) - 0xdc00))
				p.pos = afterLow
			} else if cu >= 0xdc00 && cu <= 0xdfff {
				return "", ErrInvalidUnicodeScalar
			} else {
				r = rune(cu)
			}
			n := utf8.RuneLen(r)
			if n < 0 {
				return "", ErrInvalidUnicodeScalar
			}
			decodedBytes += n
			if decodedBytes > MaxStringUTF8Bytes {
				return "", ErrCanonicalResourceLimitExceeded
			}
			out.WriteRune(r)
		default:
			return "", ErrMalformedJSON
		}
	}
	return "", ErrMalformedJSON
}

func (p *parser) skipWhitespace() {
	for p.pos < len(p.input) {
		switch p.input[p.pos] {
		case ' ', '\t', '\n', '\r':
			p.pos++
		default:
			return
		}
	}
}

func (p *parser) take(c byte) bool {
	if p.pos < len(p.input) && p.input[p.pos] == c {
		p.pos++
		return true
	}
	return false
}

func (p *parser) consumeLiteral(s string) bool {
	if len(p.input)-p.pos < len(s) || string(p.input[p.pos:p.pos+len(s)]) != s {
		return false
	}
	end := p.pos + len(s)
	if end < len(p.input) && !isValueTerminator(p.input[end]) {
		return false
	}
	p.pos = end
	return true
}

func isValueStart(c byte) bool {
	return c == '{' || c == '[' || c == '"' || c == 't' || c == 'f' || c == 'n' || c == '-' || (c >= '0' && c <= '9')
}

func isValueTerminator(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == ',' || c == ']' || c == '}'
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
