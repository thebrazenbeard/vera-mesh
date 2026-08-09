package operator

import (
	"bufio"
	"bytes"
	"encoding/json"
	"regexp"
	"strings"
	"testing"
)

var uuidV4Pattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

type decodedEvent struct {
	CommandID         any     `json:"command_id"`
	EventSeq          uint64  `json:"event_seq"`
	EventType         string  `json:"event_type"`
	ExitCode          *int    `json:"exit_code"`
	Outcome           string  `json:"outcome"`
	PriorEventCount   *uint64 `json:"prior_event_count"`
	ProcessInstanceID string  `json:"process_instance_id"`
	ProductCode       any     `json:"product_code"`
	Schema            string  `json:"schema"`
	TerminalOutcome   string  `json:"terminal_outcome"`
	WrapperCode       string  `json:"wrapper_code"`
}

func runCLI(t *testing.T, input string, args ...string) (int, []string, string) {
	t.Helper()
	var stdout, stderr bytes.Buffer
	code := Run(strings.NewReader(input), &stdout, &stderr, args)
	lines := []string{}
	s := bufio.NewScanner(bytes.NewReader(stdout.Bytes()))
	for s.Scan() {
		lines = append(lines, s.Text())
	}
	if err := s.Err(); err != nil {
		t.Fatal(err)
	}
	if stdout.Len() > 0 && !bytes.HasSuffix(stdout.Bytes(), []byte("\n")) {
		t.Fatalf("machine stream missing final LF: %q", stdout.Bytes())
	}
	return code, lines, stderr.String()
}

func decodeLine(t *testing.T, line string) decodedEvent {
	t.Helper()
	var ev decodedEvent
	if err := json.Unmarshal([]byte(line), &ev); err != nil {
		t.Fatalf("line is not JSON: %v: %q", err, line)
	}
	return ev
}

func TestQuitProducesStrictSequencedReadyAndTerminal(t *testing.T) {
	code, lines, stderr := runCLI(t, "quit\n")
	if code != 0 {
		t.Fatalf("code=%d want 0", code)
	}
	if stderr != "" {
		t.Fatalf("stderr=%q want empty", stderr)
	}
	if len(lines) != 2 {
		t.Fatalf("lines=%d want 2: %#v", len(lines), lines)
	}
	ready := decodeLine(t, lines[0])
	term := decodeLine(t, lines[1])
	if ready.Schema != SchemaV1 || ready.EventType != "PROCESS_STARTED" || ready.WrapperCode != "CLI_READY" || ready.Outcome != "OK" {
		t.Fatalf("ready=%+v", ready)
	}
	if ready.EventSeq != 0 {
		t.Fatalf("ready seq=%d", ready.EventSeq)
	}
	if !uuidV4Pattern.MatchString(ready.ProcessInstanceID) {
		t.Fatalf("process id=%q", ready.ProcessInstanceID)
	}
	if term.ProcessInstanceID != ready.ProcessInstanceID || term.EventSeq != 1 {
		t.Fatalf("term=%+v", term)
	}
	if term.EventType != "CLI_TERMINAL" || term.WrapperCode != "CLI_ORDERLY_EXIT" || term.Outcome != "OK" || term.TerminalOutcome != "ORDERLY_EXIT" || term.ExitCode == nil || *term.ExitCode != 0 || term.PriorEventCount == nil || *term.PriorEventCount != 1 {
		t.Fatalf("term=%+v", term)
	}
}

func TestStatusIsTruthfullyUnboundNotReady(t *testing.T) {
	code, lines, _ := runCLI(t, "status\nquit\n")
	if code != 0 || len(lines) != 3 {
		t.Fatalf("code=%d lines=%d", code, len(lines))
	}
	status := decodeLine(t, lines[1])
	if status.EventSeq != 1 || status.EventType != "STATUS_RESULT" || status.Outcome != "UNKNOWN" || status.WrapperCode != "CLI_DIAGNOSTICS_UNBOUND" {
		t.Fatalf("status=%+v", status)
	}
}

func TestMutatingVerbsAreDependencyBlockedWithoutProductMutation(t *testing.T) {
	verbs := []struct{ verb, event string }{
		{"pair-start", "PAIR_START_BLOCKED"},
		{"note-send", "NOTE_SEND_BLOCKED"},
		{"revoke-probe", "REVOKE_PROBE_BLOCKED"},
		{"repair-pair-start", "REPAIR_PAIR_START_BLOCKED"},
	}
	for _, tc := range verbs {
		t.Run(tc.verb, func(t *testing.T) {
			code, lines, _ := runCLI(t, tc.verb+"\nquit\n")
			if code != 0 || len(lines) != 3 {
				t.Fatalf("code=%d lines=%d", code, len(lines))
			}
			ev := decodeLine(t, lines[1])
			if ev.EventType != tc.event || ev.Outcome != "BLOCKED" || ev.WrapperCode != "CLI_DEPENDENCY_BLOCKED" || ev.ProductCode != nil {
				t.Fatalf("event=%+v", ev)
			}
		})
	}
}

func TestClosedCommandGrammarRejectsUnknownExtraAndNonASCIIWithoutEcho(t *testing.T) {
	inputs := []string{"wat secret\nquit\n", "status extra\nquit\n", "státus\nquit\n", "\nquit\n"}
	for _, input := range inputs {
		code, lines, _ := runCLI(t, input)
		if code != 0 || len(lines) != 3 {
			t.Fatalf("input=%q code=%d lines=%d", input, code, len(lines))
		}
		ev := decodeLine(t, lines[1])
		if ev.EventType != "COMMAND_REJECTED" || ev.Outcome != "REJECTED" || ev.WrapperCode != "CLI_INVALID_COMMAND" {
			t.Fatalf("input=%q event=%+v", input, ev)
		}
		if strings.Contains(lines[1], "secret") || strings.Contains(lines[1], "státus") {
			t.Fatalf("rejected input leaked into machine output: %q", lines[1])
		}
	}
}

func TestMachineRecordsUseCanonicalLexicographicObjectKeyOrder(t *testing.T) {
	_, lines, _ := runCLI(t, "status\nquit\n")
	// Canonical V2 sorts object keys lexicographically. These exact restricted
	// machine records have no nested objects, floats, or non-canonical strings.
	for _, line := range lines {
		order := []string{`"command_id":`, `"event_seq":`, `"event_type":`, `"outcome":`, `"process_instance_id":`, `"product_code":`, `"schema":`}
		last := -1
		for _, key := range order {
			idx := strings.Index(line, key)
			if idx < 0 {
				t.Fatalf("missing %s in %q", key, line)
			}
			if idx <= last {
				t.Fatalf("noncanonical key order in %q", line)
			}
			last = idx
		}
	}
}

func TestEOFStillEmitsOneTerminalRecord(t *testing.T) {
	code, lines, _ := runCLI(t, "")
	if code != 0 || len(lines) != 2 {
		t.Fatalf("code=%d lines=%d", code, len(lines))
	}
	term := decodeLine(t, lines[1])
	if term.EventType != "CLI_TERMINAL" || term.WrapperCode != "CLI_ORDERLY_EXIT" {
		t.Fatalf("term=%+v", term)
	}
}

func TestLaunchArgumentsFailTypedWithoutReadyClaim(t *testing.T) {
	code, lines, _ := runCLI(t, "", "--insecure")
	if code != 2 || len(lines) != 1 {
		t.Fatalf("code=%d lines=%d", code, len(lines))
	}
	term := decodeLine(t, lines[0])
	if term.EventType != "CLI_TERMINAL" || term.WrapperCode != "CLI_INVALID_COMMAND" || term.TerminalOutcome != "INVALID_INVOCATION" || term.ExitCode == nil || *term.ExitCode != 2 {
		t.Fatalf("term=%+v", term)
	}
}
