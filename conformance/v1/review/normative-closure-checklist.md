# Task 2 normative closure checklist

This file converts the independent hostile review into concrete acceptance checks for the primary Task 2 lane. It does not replace the normative protocol files.

1. **Recipient storage receipt visibility** — If relay GC depends on `recipient_received`, define a relay-visible, recipient-signed custody record containing only non-secret metadata. A purely HPKE-opaque receipt cannot be verified by VeraRelay. Vector: `RECEIPT-P0-003`.

2. **Relay custody trust bootstrap** — Pairing/bootstrap must give endpoints a trusted binding for relay installation identity, custody public key, and custody key epoch. Vector: `CUSTODY-P0-001`.

3. **Exact inner-signature bytes** — Freeze byte-level signing semantics; verification must not depend on parsing then reserializing JSON. Vector: `E2E-P1-002`.

4. **Cross-relay request binding** — Protected requests need a signed binding to the intended relay installation (or an equivalently stable signed authority component). Vector: `AUTH-P1-005`.

5. **Freshness boundaries** — Freeze exact `created`/`expires` acceptance inequalities, allowed skew, and maximum lifetime. Vector: `FRESH-P2-001`.

6. **Custody retry equivalence** — Decide whether exact signed custody receipt bytes are persisted. If not, retries are equivalent by immutable signed claims, not by byte-identical ECDSA output. Vector: `MAIL-P1-002`.

7. **Content-Digest policy** — Freeze whether extra RFC 9530 dictionary members are rejected or ignored when a valid `sha-256` member exists. Vector: `DIGEST-P2-002`.

## Immediate no-ambiguity blockers

The following cases are already fully determined by the approved design and should become executable negative tests without waiting for further architecture work: `AUTH-P0-001`, `AUTH-P0-002`, `AUTH-P0-003`, `AUTH-P1-004`, `DIGEST-P1-001`, `PAIR-P0-001`, `PAIR-P0-002`, `E2E-P0-001`, `MAIL-P0-001`, `RECEIPT-P0-001`, `RECEIPT-P0-002`, `RECOVERY-P1-001`, `RECOVERY-P0-002`, and `KEY-P0-001`.

A Task 2 implementation should not be called interoperable until every `requires_normative_closure: true` case has been resolved into one unambiguous rule and at least one positive and one hostile-negative machine vector.