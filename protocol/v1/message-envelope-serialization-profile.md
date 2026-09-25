# VeraMesh V1 message-envelope serialization profile

Status: normative.

All hash inputs in this profile are UTF-8 bytes produced by RFC 8785 JSON Canonicalization Scheme (JCS). The wire JSON may contain ordinary insignificant whitespace, but implementations MUST parse the object and construct the exact JCS bytes below for hash computation. They MUST NOT substitute a language-default serializer, a pretty-printer, or an ad-hoc key sorter.

V1 envelope objects are restricted to I-JSON-compatible strings, safe integers, arrays, and objects. No floating-point value, duplicate member name, non-UTF-8 text, or non-finite number is permitted in a hashed object. JCS is applied recursively, with lexicographic property ordering and no insignificant whitespace.

## AAD bytes

The authenticated-data bytes are exactly:

UTF8(JCS(aad_binding))

aad_binding MUST contain the eleven fields defined by message-envelope.schema.json, and its values MUST equal the corresponding outer envelope fields. aad_sha256 is the lowercase hexadecimal SHA-256 digest of those exact bytes.

## Envelope bytes

The immutable envelope hash input is exactly the outer envelope object with only the envelope_sha256 member removed:

UTF8(JCS(envelope_without_envelope_sha256))

All remaining members, including aad_binding and aad_sha256, remain in the hash input. envelope_sha256 is the lowercase hexadecimal SHA-256 digest of those exact bytes. A verifier MUST recompute it over the received object before accepting or comparing immutable message identity.

## Ciphertext bytes

ciphertext_sha256 is the lowercase hexadecimal SHA-256 digest of the raw bytes obtained by strict base64 decoding ciphertext_base64. Base64 text spelling is not itself the ciphertext hash input.

protocol/v1/vectors/message-envelope-vectors.json provides fixed canonical UTF-8 byte strings and expected digests for both protocol directions.
## I-JSON integer boundary

Every integer-valued field in a hashed V1 envelope is limited to the I-JSON/JCS interoperable safe range 1 through 9007199254740991 (2^53-1), as enforced by the schemas. The first value above that range, 9007199254740992, is rejected. Implementations MUST encode a value outside a schema-defined integer field as a protocol-defined string or omit it; they MUST NOT hash an out-of-range JSON number as if it were portable.
