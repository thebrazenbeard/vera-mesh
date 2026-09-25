# VeraPort response wire-size profile V1

Status: SOURCE CONTRACT / PARTIAL ISSUE #21 REPAIR / NOT INSTALLED

## Final response bound

The transport `max_frame_bytes` limit is the final response-wire authority.

A successful local operation does not imply that its JSON response is wire-admissible.
Every response is serialized with the exact same strict JSON policy used by the wire
writer before bytes are attempted on the stream.

The serializer uses:
- UTF-8;
- sorted keys;
- compact separators;
- `ensure_ascii=True`.

Therefore source-byte size, text-character count, and encoded response size are distinct.

## Oversized handler result

If an otherwise successful handler result exceeds `max_frame_bytes`, the server does
not attempt the oversized frame. It replaces it with a correlated bounded error:

`RESPONSE_FRAME_TOO_LARGE`

The error retains the request ID when that request ID itself fits the response policy.

If the bounded error cannot itself be written, or if the transport write/drain fails,
the server closes the stream. Client pending futures must then fail through stream
closure / their already-bounded operation deadline rather than hang indefinitely.

## Serialization failure

A handler result that is not strict JSON-serializable is converted to the correlated
error:

`RESPONSE_SERIALIZATION_ERROR`

This is distinct from handler/application failure.

## File-read boundary

`LocalExecutor.max_read_bytes` remains a workstation-local admission bound. It is not
a promise that `fs.read_text` can always fit in one wire frame because JSON escaping and
response metadata add bytes.

This source cut makes that mismatch deterministic and correlated. It does not claim that
all-at-once text reads provide practical large-file transfer.

Ranged/chunked reading remains a separate capability increment under Issue #21 / Issue
#14 and must re-check path, lane, fence, and root authority for every chunk.

## Claim ceiling

This contract establishes source-level wire failure semantics only.

It does not establish executable qualification, workstation installation, current route,
live file-read success, MCP registration, tunnel state, write/process authority, or
RDC-equivalence.
