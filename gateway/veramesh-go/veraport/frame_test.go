package veraport

import (
	"bytes"
	"encoding/binary"
	"strings"
	"testing"
)

func TestFrameRoundTripAndBounds(t *testing.T) {
	value := map[string]any{
		"protocol_version": ProtocolVersion,
		"request_id": "r1",
		"operation": "lane.list",
	}
	frame, err := EncodeFrame(value, DefaultMaxFrameBytes)
	if err != nil { t.Fatal(err) }
	if int(binary.BigEndian.Uint32(frame[:4])) != len(frame)-4 {
		t.Fatal("frame length prefix mismatch")
	}
	var got map[string]any
	if err := ReadFrame(bytes.NewReader(frame), &got, DefaultMaxFrameBytes); err != nil {
		t.Fatal(err)
	}
	if got["request_id"] != "r1" {
		t.Fatalf("round trip mismatch: %#v", got)
	}
	if _, err := EncodeFrame(value, 2); err == nil {
		t.Fatal("oversized frame accepted")
	}
}

func TestFrameRejectsInvalidUTF8AndInvalidLength(t *testing.T) {
	var bad bytes.Buffer
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], 2)
	bad.Write(header[:])
	bad.Write([]byte{0xff, 0xfe})
	var target map[string]any
	if err := ReadFrame(&bad, &target, 100); err == nil || !strings.Contains(err.Error(), "UTF-8") {
		t.Fatalf("invalid UTF-8 not rejected: %v", err)
	}

	bad.Reset()
	binary.BigEndian.PutUint32(header[:], uint32(DefaultMaxFrameBytes+1))
	bad.Write(header[:])
	if err := ReadFrame(&bad, &target, DefaultMaxFrameBytes); err == nil {
		t.Fatal("oversized declared frame accepted")
	}
}
