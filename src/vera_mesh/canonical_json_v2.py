"""Strict received-wire Canonical JSON V2 parser and emitter.

This module owns raw JSON byte admission. It deliberately does not use
``json.loads`` because duplicate member spelling, lexical number form, resource
precedence, and invalid UTF-8 must be decided before Python materializes values.
"""

from __future__ import annotations

from typing import Any

from .digests import DigestValidationError

CANONICALIZATION_ID = "VERA_MESH_CANONICAL_JSON_V2"
RESOURCE_PROFILE_ID = "VERA_MESH_CANONICAL_RESOURCE_LIMITS_FIRST_SLICE_V1"

MAX_INPUT_BYTES = 65536
MAX_NESTING_DEPTH = 8
MAX_OBJECT_MEMBERS = 32
MAX_ARRAY_ELEMENTS = 32
MAX_STRING_UTF8_BYTES = 32768

_WS = frozenset((0x20, 0x09, 0x0A, 0x0D))
_HEX = b"0123456789abcdefABCDEF"
_DISCARDED = object()


class CanonicalJsonV2Error(DigestValidationError):
    """Typed fail-closed rejection for received-wire Canonical JSON V2."""

    def __init__(self, code: str, *, offset: int | None = None) -> None:
        self.code = code
        self.offset = offset
        suffix = "" if offset is None else f" at byte {offset}"
        super().__init__(f"{code}{suffix}")


class _Parser:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.pos = 0

    def fail(self, code: str, *, offset: int | None = None) -> None:
        raise CanonicalJsonV2Error(code, offset=self.pos if offset is None else offset)

    def skip_ws(self) -> None:
        raw = self.raw
        pos = self.pos
        while pos < len(raw) and raw[pos] in _WS:
            pos += 1
        self.pos = pos

    def classify_unexpected(self) -> None:
        if self.pos >= len(self.raw):
            self.fail("MALFORMED_JSON")
        byte = self.raw[self.pos]
        if byte >= 0x80:
            self.decode_utf8_scalar(self.pos)
        self.fail("MALFORMED_JSON")

    def decode_utf8_scalar(self, start: int) -> tuple[str, int]:
        raw = self.raw
        if start >= len(raw):
            self.fail("INVALID_UTF8", offset=start)
        first = raw[start]
        if 0xC2 <= first <= 0xDF:
            size = 2
        elif 0xE0 <= first <= 0xEF:
            size = 3
        elif 0xF0 <= first <= 0xF4:
            size = 4
        else:
            self.fail("INVALID_UTF8", offset=start)
        end = start + size
        if end > len(raw):
            self.fail("INVALID_UTF8", offset=start)
        piece = raw[start:end]
        try:
            text = piece.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self.fail("INVALID_UTF8", offset=start)
        if len(text) != 1:
            self.fail("INVALID_UTF8", offset=start)
        codepoint = ord(text)
        if 0xD800 <= codepoint <= 0xDFFF:
            self.fail("INVALID_UNICODE_SCALAR", offset=start)
        return text, end

    def parse(self) -> Any:
        self.skip_ws()
        if self.pos >= len(self.raw):
            self.fail("MALFORMED_JSON")
        value = self.parse_value(depth=0, retain=True)
        self.skip_ws()
        if self.pos != len(self.raw):
            self.classify_unexpected()
        return value

    def parse_value(self, *, depth: int, retain: bool) -> Any:
        self.skip_ws()
        if self.pos >= len(self.raw):
            self.fail("MALFORMED_JSON")
        byte = self.raw[self.pos]
        if byte == ord("{"):
            if depth + 1 > MAX_NESTING_DEPTH:
                self.fail("CANONICAL_RESOURCE_LIMIT_EXCEEDED")
            return self.parse_object(depth=depth + 1, retain=retain)
        if byte == ord("["):
            if depth + 1 > MAX_NESTING_DEPTH:
                self.fail("CANONICAL_RESOURCE_LIMIT_EXCEEDED")
            return self.parse_array(depth=depth + 1, retain=retain)
        if byte == ord('"'):
            return self.parse_string(retain=retain)
        if byte == ord("t"):
            self.consume_literal(b"true")
            return True if retain else _DISCARDED
        if byte == ord("f"):
            self.consume_literal(b"false")
            return False if retain else _DISCARDED
        if byte == ord("n"):
            self.consume_literal(b"null")
            return None if retain else _DISCARDED
        if byte == ord("-") or ord("0") <= byte <= ord("9"):
            return self.parse_number(retain=retain)
        self.classify_unexpected()

    def consume_literal(self, literal: bytes) -> None:
        start = self.pos
        for expected in literal:
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON", offset=start)
            actual = self.raw[self.pos]
            if actual != expected:
                if actual >= 0x80:
                    self.decode_utf8_scalar(self.pos)
                self.fail("MALFORMED_JSON")
            self.pos += 1
        if self.pos < len(self.raw) and not self.is_value_boundary(self.raw[self.pos]):
            self.classify_unexpected()

    @staticmethod
    def is_value_boundary(byte: int) -> bool:
        return byte in _WS or byte in (ord(","), ord("]"), ord("}"))

    def parse_number(self, *, retain: bool) -> Any:
        raw = self.raw
        start = self.pos
        if raw[self.pos] == ord("-"):
            self.pos += 1
            if self.pos >= len(raw):
                self.fail("MALFORMED_JSON", offset=start)

        if self.pos >= len(raw):
            self.fail("MALFORMED_JSON", offset=start)
        first_digit = raw[self.pos]
        if first_digit == ord("0"):
            self.pos += 1
            if self.pos < len(raw) and ord("0") <= raw[self.pos] <= ord("9"):
                self.fail("MALFORMED_JSON")
        elif ord("1") <= first_digit <= ord("9"):
            self.pos += 1
            while self.pos < len(raw) and ord("0") <= raw[self.pos] <= ord("9"):
                self.pos += 1
        else:
            if first_digit >= 0x80:
                self.decode_utf8_scalar(self.pos)
            self.fail("MALFORMED_JSON")

        if self.pos < len(raw) and raw[self.pos] in (ord("."), ord("e"), ord("E")):
            self.parse_float_tail()
            self.fail("FLOAT_FORBIDDEN", offset=start)

        if self.pos < len(raw) and not self.is_value_boundary(raw[self.pos]):
            self.classify_unexpected()

        if not retain:
            return _DISCARDED
        token = raw[start:self.pos]
        return b"0" if token == b"-0" else token

    def parse_float_tail(self) -> None:
        raw = self.raw
        if self.pos < len(raw) and raw[self.pos] == ord("."):
            self.pos += 1
            if self.pos >= len(raw) or not (ord("0") <= raw[self.pos] <= ord("9")):
                if self.pos < len(raw) and raw[self.pos] >= 0x80:
                    self.decode_utf8_scalar(self.pos)
                self.fail("MALFORMED_JSON")
            while self.pos < len(raw) and ord("0") <= raw[self.pos] <= ord("9"):
                self.pos += 1
        if self.pos < len(raw) and raw[self.pos] in (ord("e"), ord("E")):
            self.pos += 1
            if self.pos < len(raw) and raw[self.pos] in (ord("+"), ord("-")):
                self.pos += 1
            if self.pos >= len(raw) or not (ord("0") <= raw[self.pos] <= ord("9")):
                if self.pos < len(raw) and raw[self.pos] >= 0x80:
                    self.decode_utf8_scalar(self.pos)
                self.fail("MALFORMED_JSON")
            while self.pos < len(raw) and ord("0") <= raw[self.pos] <= ord("9"):
                self.pos += 1
        if self.pos < len(raw) and not self.is_value_boundary(raw[self.pos]):
            self.classify_unexpected()

    def require_colon(self) -> None:
        self.skip_ws()
        if self.pos >= len(self.raw) or self.raw[self.pos] != ord(":"):
            self.classify_unexpected()
        self.pos += 1

    def validate_value_delimiter(self, closing: int) -> None:
        self.skip_ws()
        if self.pos >= len(self.raw):
            self.fail("MALFORMED_JSON")
        if self.raw[self.pos] not in (closing, ord(",")):
            self.classify_unexpected()

    def validate_excess_object_member(self, *, depth: int) -> None:
        # A limit result is valid only after a complete 33rd member is proven.
        self.parse_string(retain=False)
        self.require_colon()
        self.parse_value(depth=depth, retain=False)
        self.validate_value_delimiter(ord("}"))
        self.fail("CANONICAL_RESOURCE_LIMIT_EXCEEDED")

    def parse_object(self, *, depth: int, retain: bool) -> Any:
        self.pos += 1
        result: dict[str, Any] | None = {} if retain else None
        seen: set[str] = set()
        self.skip_ws()
        if self.pos < len(self.raw) and self.raw[self.pos] == ord("}"):
            self.pos += 1
            return result if retain else _DISCARDED

        member_count = 0
        while True:
            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            if self.raw[self.pos] != ord('"'):
                self.classify_unexpected()
            if member_count >= MAX_OBJECT_MEMBERS:
                self.validate_excess_object_member(depth=depth)

            key = self.parse_string(retain=True)
            assert isinstance(key, str)
            member_count += 1
            if key in seen:
                self.fail("DUPLICATE_OBJECT_KEY")
            seen.add(key)

            self.require_colon()
            value = self.parse_value(depth=depth, retain=retain)
            if result is not None:
                result[key] = value

            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            byte = self.raw[self.pos]
            if byte == ord("}"):
                self.pos += 1
                return result if retain else _DISCARDED
            if byte != ord(","):
                self.classify_unexpected()
            self.pos += 1
            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            if self.raw[self.pos] == ord("}"):
                self.fail("MALFORMED_JSON")

    def validate_excess_array_element(self, *, depth: int) -> None:
        # Parse-and-discard proves the 33rd lexical value before returning limit.
        self.parse_value(depth=depth, retain=False)
        self.validate_value_delimiter(ord("]"))
        self.fail("CANONICAL_RESOURCE_LIMIT_EXCEEDED")

    def parse_array(self, *, depth: int, retain: bool) -> Any:
        self.pos += 1
        result: list[Any] | None = [] if retain else None
        element_count = 0
        self.skip_ws()
        if self.pos < len(self.raw) and self.raw[self.pos] == ord("]"):
            self.pos += 1
            return result if retain else _DISCARDED

        while True:
            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            if self.raw[self.pos] == ord("]"):
                self.fail("MALFORMED_JSON")
            if element_count >= MAX_ARRAY_ELEMENTS:
                self.validate_excess_array_element(depth=depth)

            value = self.parse_value(depth=depth, retain=retain)
            element_count += 1
            if result is not None:
                result.append(value)

            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            byte = self.raw[self.pos]
            if byte == ord("]"):
                self.pos += 1
                return result if retain else _DISCARDED
            if byte != ord(","):
                self.classify_unexpected()
            self.pos += 1
            self.skip_ws()
            if self.pos >= len(self.raw):
                self.fail("MALFORMED_JSON")
            if self.raw[self.pos] == ord("]"):
                self.fail("MALFORMED_JSON")

    def parse_string(self, *, retain: bool) -> Any:
        raw = self.raw
        if self.pos >= len(raw) or raw[self.pos] != ord('"'):
            self.fail("MALFORMED_JSON")
        self.pos += 1
        pieces: list[str] | None = [] if retain else None
        utf8_bytes = 0

        while True:
            if self.pos >= len(raw):
                self.fail("MALFORMED_JSON")
            byte = raw[self.pos]
            if byte == ord('"'):
                self.pos += 1
                return "".join(pieces) if pieces is not None else _DISCARDED
            if byte < 0x20:
                self.fail("MALFORMED_JSON")

            if byte == ord("\\"):
                char = self.parse_escape()
            elif byte < 0x80:
                char = chr(byte)
                self.pos += 1
            else:
                char, self.pos = self.decode_utf8_scalar(self.pos)

            char_bytes = len(char.encode("utf-8"))
            if utf8_bytes + char_bytes > MAX_STRING_UTF8_BYTES:
                self.fail("CANONICAL_RESOURCE_LIMIT_EXCEEDED")
            utf8_bytes += char_bytes
            if pieces is not None:
                pieces.append(char)

    def parse_escape(self) -> str:
        raw = self.raw
        start = self.pos
        self.pos += 1
        if self.pos >= len(raw):
            self.fail("MALFORMED_JSON", offset=start)
        esc = raw[self.pos]
        self.pos += 1
        simple = {
            ord('"'): '"',
            ord("\\"): "\\",
            ord("/"): "/",
            ord("b"): "\b",
            ord("f"): "\f",
            ord("n"): "\n",
            ord("r"): "\r",
            ord("t"): "\t",
        }
        if esc in simple:
            return simple[esc]
        if esc != ord("u"):
            if esc >= 0x80:
                self.decode_utf8_scalar(self.pos - 1)
            self.fail("MALFORMED_JSON", offset=start)

        first = self.parse_hex_code_unit()
        if 0xD800 <= first <= 0xDBFF:
            if self.pos + 2 > len(raw) or raw[self.pos:self.pos + 2] != b"\\u":
                self.fail("INVALID_UNICODE_SCALAR", offset=start)
            self.pos += 2
            second = self.parse_hex_code_unit()
            if not 0xDC00 <= second <= 0xDFFF:
                self.fail("INVALID_UNICODE_SCALAR", offset=start)
            scalar = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
            return chr(scalar)
        if 0xDC00 <= first <= 0xDFFF:
            self.fail("INVALID_UNICODE_SCALAR", offset=start)
        return chr(first)

    def parse_hex_code_unit(self) -> int:
        start = self.pos
        end = start + 4
        if end > len(self.raw):
            self.fail("MALFORMED_JSON", offset=start)
        digits = self.raw[start:end]
        for index, byte in enumerate(digits):
            if byte not in _HEX:
                if byte >= 0x80:
                    self.decode_utf8_scalar(start + index)
                self.fail("MALFORMED_JSON", offset=start + index)
        self.pos = end
        return int(digits.decode("ascii"), 16)


def _emit_string(value: str, out: bytearray) -> None:
    out.append(ord('"'))
    short = {
        0x08: b"\\b",
        0x09: b"\\t",
        0x0A: b"\\n",
        0x0C: b"\\f",
        0x0D: b"\\r",
    }
    for char in value:
        codepoint = ord(char)
        if codepoint == 0x22:
            out.extend(b'\\"')
        elif codepoint == 0x5C:
            out.extend(b"\\\\")
        elif codepoint in short:
            out.extend(short[codepoint])
        elif codepoint < 0x20:
            out.extend(f"\\u{codepoint:04x}".encode("ascii"))
        else:
            out.extend(char.encode("utf-8"))
    out.append(ord('"'))


def _emit(value: Any, out: bytearray) -> None:
    if value is None:
        out.extend(b"null")
    elif value is True:
        out.extend(b"true")
    elif value is False:
        out.extend(b"false")
    elif isinstance(value, bytes):
        out.extend(value)
    elif isinstance(value, str):
        _emit_string(value, out)
    elif isinstance(value, list):
        out.append(ord("["))
        for index, item in enumerate(value):
            if index:
                out.append(ord(","))
            _emit(item, out)
        out.append(ord("]"))
    elif isinstance(value, dict):
        out.append(ord("{"))
        for index, key in enumerate(sorted(value)):
            if index:
                out.append(ord(","))
            _emit_string(key, out)
            out.append(ord(":"))
            _emit(value[key], out)
        out.append(ord("}"))
    else:  # pragma: no cover - parser cannot create another type
        raise AssertionError(f"unsupported parsed type {type(value).__name__}")


def canonicalize_json_v2(raw: bytes) -> bytes:
    """Parse strict received JSON bytes and return exact Canonical JSON V2 bytes."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_INPUT_BYTES:
        raise CanonicalJsonV2Error("CANONICAL_RESOURCE_LIMIT_EXCEEDED", offset=MAX_INPUT_BYTES)
    parsed = _Parser(raw).parse()
    out = bytearray()
    _emit(parsed, out)
    return bytes(out)
