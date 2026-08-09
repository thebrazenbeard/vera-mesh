package operator

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"strconv"
	"unicode/utf8"
)

const (
	SchemaV1            = "VERA_MESH_WINDOWS_OPERATOR_EVENT_V1"
	maxCommandLineBytes = 4096
)

type machineRecord struct {
	commandID         *string
	eventSeq          uint64
	eventType         string
	exitCode          *int
	outcome           string
	priorEventCount   *uint64
	processInstanceID string
	productCode       *string
	terminalOutcome   *string
	wrapperCode       string
}

type stream struct {
	out       io.Writer
	processID string
	seq       uint64
}

func Run(input io.Reader, machineOut io.Writer, humanOut io.Writer, args []string) int {
	_ = humanOut // Reserved for fixed-template nonauthoritative text only.

	processID, err := newProcessInstanceID()
	if err != nil {
		return 70
	}
	s := &stream{out: machineOut, processID: processID}

	if len(args) != 0 {
		if err := s.terminal("REJECTED", "CLI_INVALID_COMMAND", "INVALID_INVOCATION", 2); err != nil {
			return 70
		}
		return 2
	}

	if err := s.emit(machineRecord{
		eventType:   "PROCESS_STARTED",
		outcome:     "OK",
		wrapperCode: "CLI_READY",
	}); err != nil {
		return 70
	}

	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 128), maxCommandLineBytes)
	for scanner.Scan() {
		line := scanner.Text()
		switch line {
		case "status":
			if err := s.emit(machineRecord{
				eventType:   "STATUS_RESULT",
				outcome:     "UNKNOWN",
				wrapperCode: "CLI_DIAGNOSTICS_UNBOUND",
			}); err != nil {
				return 70
			}
		case "pair-start":
			if err := s.blocked("PAIR_START_BLOCKED"); err != nil {
				return 70
			}
		case "note-send":
			if err := s.blocked("NOTE_SEND_BLOCKED"); err != nil {
				return 70
			}
		case "revoke-probe":
			if err := s.blocked("REVOKE_PROBE_BLOCKED"); err != nil {
				return 70
			}
		case "repair-pair-start":
			if err := s.blocked("REPAIR_PAIR_START_BLOCKED"); err != nil {
				return 70
			}
		case "quit":
			if err := s.terminal("OK", "CLI_ORDERLY_EXIT", "ORDERLY_EXIT", 0); err != nil {
				return 70
			}
			return 0
		default:
			if err := s.emit(machineRecord{
				eventType:   "COMMAND_REJECTED",
				outcome:     "REJECTED",
				wrapperCode: "CLI_INVALID_COMMAND",
			}); err != nil {
				return 70
			}
		}
	}
	if err := scanner.Err(); err != nil {
		if terminalErr := s.terminal("FAILED", "CLI_INTERNAL_ERROR", "INTERNAL_FAIL_CLOSED", 70); terminalErr != nil {
			return 70
		}
		return 70
	}
	if err := s.terminal("OK", "CLI_ORDERLY_EXIT", "ORDERLY_EXIT", 0); err != nil {
		return 70
	}
	return 0
}

func (s *stream) blocked(eventType string) error {
	return s.emit(machineRecord{
		eventType:   eventType,
		outcome:     "BLOCKED",
		wrapperCode: "CLI_DEPENDENCY_BLOCKED",
	})
}

func (s *stream) terminal(outcome, wrapper, terminalOutcome string, exitCode int) error {
	prior := s.seq
	return s.emit(machineRecord{
		eventType:       "CLI_TERMINAL",
		exitCode:        &exitCode,
		outcome:         outcome,
		priorEventCount: &prior,
		terminalOutcome: &terminalOutcome,
		wrapperCode:     wrapper,
	})
}

func (s *stream) emit(rec machineRecord) error {
	rec.eventSeq = s.seq
	rec.processInstanceID = s.processID
	encoded, err := marshalCanonical(rec)
	if err != nil {
		return err
	}
	encoded = append(encoded, '\n')
	n, err := s.out.Write(encoded)
	if err != nil {
		return err
	}
	if n != len(encoded) {
		return io.ErrShortWrite
	}
	s.seq++
	return nil
}

func marshalCanonical(rec machineRecord) ([]byte, error) {
	for _, value := range []string{rec.eventType, rec.outcome, rec.processInstanceID, rec.wrapperCode} {
		if !utf8.ValidString(value) {
			return nil, errors.New("invalid machine string")
		}
	}
	if rec.commandID != nil && !utf8.ValidString(*rec.commandID) {
		return nil, errors.New("invalid command id")
	}
	if rec.productCode != nil && !utf8.ValidString(*rec.productCode) {
		return nil, errors.New("invalid product code")
	}
	if rec.terminalOutcome != nil && !utf8.ValidString(*rec.terminalOutcome) {
		return nil, errors.New("invalid terminal outcome")
	}

	var out bytes.Buffer
	out.WriteByte('{')
	first := true
	field := func(name string, writeValue func()) {
		if !first {
			out.WriteByte(',')
		}
		first = false
		appendJSONString(&out, name)
		out.WriteByte(':')
		writeValue()
	}
	stringOrNull := func(value *string) func() {
		return func() {
			if value == nil {
				out.WriteString("null")
				return
			}
			appendJSONString(&out, *value)
		}
	}
	stringValue := func(value string) func() {
		return func() { appendJSONString(&out, value) }
	}

	// Canonical JSON V2 object keys are emitted in lexicographic scalar order.
	field("command_id", stringOrNull(rec.commandID))
	field("event_seq", func() { out.WriteString(strconv.FormatUint(rec.eventSeq, 10)) })
	field("event_type", stringValue(rec.eventType))
	if rec.exitCode != nil {
		field("exit_code", func() { out.WriteString(strconv.Itoa(*rec.exitCode)) })
	}
	field("outcome", stringValue(rec.outcome))
	if rec.priorEventCount != nil {
		field("prior_event_count", func() { out.WriteString(strconv.FormatUint(*rec.priorEventCount, 10)) })
	}
	field("process_instance_id", stringValue(rec.processInstanceID))
	field("product_code", stringOrNull(rec.productCode))
	field("schema", stringValue(SchemaV1))
	if rec.terminalOutcome != nil {
		field("terminal_outcome", stringValue(*rec.terminalOutcome))
	}
	field("wrapper_code", stringValue(rec.wrapperCode))
	out.WriteByte('}')
	return out.Bytes(), nil
}

func appendJSONString(out *bytes.Buffer, value string) {
	out.WriteByte('"')
	for _, r := range value {
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
			if r < 0x20 {
				out.WriteString(`\u00`)
				const digits = "0123456789abcdef"
				out.WriteByte(digits[byte(r)>>4])
				out.WriteByte(digits[byte(r)&0x0f])
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
}

func newProcessInstanceID() (string, error) {
	var raw [16]byte
	if _, err := io.ReadFull(rand.Reader, raw[:]); err != nil {
		return "", err
	}
	raw[6] = (raw[6] & 0x0f) | 0x40
	raw[8] = (raw[8] & 0x3f) | 0x80

	var text [36]byte
	hex.Encode(text[0:8], raw[0:4])
	text[8] = '-'
	hex.Encode(text[9:13], raw[4:6])
	text[13] = '-'
	hex.Encode(text[14:18], raw[6:8])
	text[18] = '-'
	hex.Encode(text[19:23], raw[8:10])
	text[23] = '-'
	hex.Encode(text[24:36], raw[10:16])
	return string(text[:]), nil
}
