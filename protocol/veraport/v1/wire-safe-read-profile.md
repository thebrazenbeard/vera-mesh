# VeraPort wire-safe read profile V1

Status: SOURCE CONTRACT / NOT INSTALLED BY SOURCE PRESENCE

## Problem boundary

A workstation-local file byte limit is not an encoded response-frame limit.

VeraPort JSON frames use UTF-8 JSON with `ensure_ascii=true`. Wrapping file content in a
protocol response can therefore make the serialized frame larger than the original file
payload. Quotes, control characters, and non-ASCII text may expand further.

`LOCAL_READ_ACCEPTED != RESPONSE_FRAME_FITS`

## Legacy `fs.read_text`

`fs.read_text` remains a bounded whole-file text primitive.

The workstation still enforces `max_read_bytes` before returning complete file content.
The stream layer independently enforces `max_frame_bytes` on the encoded response.

If a handler result exceeds the frame policy:

1. the server attempts a small correlated error using the original `request_id` and
   `FRAME_TOO_LARGE`;
2. if even that error cannot fit the configured frame ceiling, the server closes the
   stream;
3. client pending requests must therefore resolve to a response or stream failure under
   their request deadline rather than remain orphaned.

Increasing the global frame ceiling is not the repair contract.

## Ranged byte primitive

`fs.read_bytes` is the preferred bounded primitive for large-file inspection.

Request fields:

- `lane_id`
- `fencing_token`
- `path`
- `offset`: non-negative byte offset, default 0
- `max_bytes`: optional positive byte count bounded by workstation
  `max_read_chunk_bytes`
- `expected_file_version`: optional token returned by an earlier chunk

Response fields:

- `content_base64`
- `offset`
- `bytes_read`
- `next_offset`
- `eof`
- `size_bytes`
- `file_version`

The reference workstation default `max_read_chunk_bytes` is 262,144 bytes. Base64 is
used so arbitrary file bytes do not depend on text encoding boundaries.

Every chunk request re-runs canonical root resolution, lane/fencing authorization, and
the `fs.read` resource claim check.

## File-version binding

The reference implementation derives `file_version` from the opened file's device,
inode, byte length, and nanosecond mtime and hashes that tuple with SHA-256.

A caller should pin the first returned token and send it as
`expected_file_version` on later chunks. A mismatch fails with
`FILE_VERSION_CHANGED`.

The implementation also compares the opened file's version before and after each read
and rejects a chunk if it changes during the operation.

This token is a local consistency guard, not a cryptographic content digest or remote
attestation. A stronger adversarial file-content identity can be added separately if
required.

## Transport-size composition

The bounded byte primitive reduces response-size variance but does not bypass the
stream's encoded-frame policy. An unusually small configured `max_frame_bytes` can
still reject a requested chunk. The deterministic overflow rule above remains
controlling.

Callers may retry a read-only chunk with a smaller `max_bytes` while preserving the
same `expected_file_version`.

Mutation retry rules are unchanged.

## Authority

`fs.read_bytes` uses the existing `fs.read` capability. It does not create a new
authority class.

Tool discovery, ranged-read availability, transport reachability, or a returned file
version do not grant filesystem authority.

## Claim ceiling

This profile describes source behavior only.

It does not establish workstation installation, live route, live filesystem access,
MCP registration, deployment, credential state, write/process authority, or
RDC-equivalence.


## Serialization and transport failure

The server preflights handler output with the same strict JSON serializer used by the
wire writer.

If handler output is not JSON-serializable, the server returns a correlated
`RESPONSE_SERIALIZATION_ERROR` when that bounded error itself fits the frame policy.

If a bounded error cannot fit, or an actual response write/drain fails, the server closes
the stream. It does not leave a request task failed while keeping the transport
apparently usable. Pending client calls therefore resolve through stream failure or their
existing request deadline.

This strengthens the legacy whole-text overflow rule without changing ranged-read
filesystem authority.
