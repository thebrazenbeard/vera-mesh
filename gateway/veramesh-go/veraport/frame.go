package veraport

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"unicode/utf8"
)

const DefaultMaxFrameBytes = 1_048_576

func EncodeFrame(value any, maxFrameBytes int) ([]byte, error) {
	if maxFrameBytes <= 0 {
		maxFrameBytes = DefaultMaxFrameBytes
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, fmt.Errorf("frame JSON serialization failed: %w", err)
	}
	if len(raw) < 2 || len(raw) > maxFrameBytes {
		return nil, fmt.Errorf("frame size %d exceeds policy", len(raw))
	}
	out := make([]byte, 4+len(raw))
	binary.BigEndian.PutUint32(out[:4], uint32(len(raw)))
	copy(out[4:], raw)
	return out, nil
}

func WriteFrame(w io.Writer, value any, maxFrameBytes int) error {
	frame, err := EncodeFrame(value, maxFrameBytes)
	if err != nil {
		return err
	}
	for len(frame) > 0 {
		n, err := w.Write(frame)
		if err != nil {
			return err
		}
		if n <= 0 {
			return io.ErrShortWrite
		}
		frame = frame[n:]
	}
	return nil
}

func ReadFrame(r io.Reader, target any, maxFrameBytes int) error {
	if maxFrameBytes <= 0 {
		maxFrameBytes = DefaultMaxFrameBytes
	}
	var header [4]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return fmt.Errorf("stream closed while reading frame header: %w", err)
	}
	size := int(binary.BigEndian.Uint32(header[:]))
	if size < 2 || size > maxFrameBytes {
		return fmt.Errorf("frame size %d exceeds policy", size)
	}
	raw := make([]byte, size)
	if _, err := io.ReadFull(r, raw); err != nil {
		return fmt.Errorf("stream closed while reading frame body: %w", err)
	}
	if !utf8.Valid(raw) {
		return errors.New("frame is not strict UTF-8 JSON")
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(target); err != nil {
		return fmt.Errorf("frame is not strict JSON: %w", err)
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		if err == nil {
			return errors.New("frame contains multiple JSON values")
		}
		return fmt.Errorf("frame has trailing invalid JSON: %w", err)
	}
	return nil
}
