# VeraMesh V1 hostile traceability

Status: `NON_NORMATIVE / REVIEW AID`

This matrix checks whether the approved V1 design has at least one hostile or recovery-oriented review case before implementation evidence is accepted. It is deliberately separate from the normative `protocol/v1` contract.

| Approved design obligation | Review vector(s) | Coverage state |
| --- | --- | --- |
| Strict actual EC P-256 validation | `AUTH-P0-001`, `AUTH-P0-002` | covered |
| Authentication grants no authority | `AUTH-P0-003` | covered |
| Nonce replay rejection | `AUTH-P1-004` | covered |
| Relay/destination binding | `AUTH-P1-005` | needs Task 2 closure |
| Required RFC 9421 components/params | `AUTH-P0-006` | covered structurally |
| Body digest integrity | `DIGEST-P1-001` | covered |
| SHA-256-only digest parsing rule | `DIGEST-P2-002` | needs Task 2 closure |
| Deterministic freshness window | `FRESH-P2-001` | needs Task 2 closure |
| Atomic single-use pairing | `PAIR-P0-001` | covered |
| Pairing cannot escalate role/scope | `PAIR-P0-002` | covered |
| Pairing keyid derived from SPKI | `PAIR-P0-003` | covered |
| Expired pairing rejected | `PAIR-P1-004` | covered |
| HPKE does not prove sender authorship | `E2E-P0-001` | covered |
| Exact inner-signature byte semantics | `E2E-P1-002` | needs Task 2 closure |
| Same ID + different envelope conflicts | `MAIL-P0-001` | covered |
| Lost acceptance response is safely retryable | `MAIL-P1-002` | needs Task 2 closure |
| Recipient isolation | `MAIL-P0-003` | covered |
| Sequence gaps observable/nonblocking | `MAIL-P1-004` | covered |
| Durable inbox write precedes receipt | `RECEIPT-P0-001` | covered |
| Relay cannot forge recipient receipt | `RECEIPT-P0-002` | covered |
| Relay-visible storage evidence if GC depends on it | `RECEIPT-P0-003` | needs Task 2 closure |
| Processed does not silently imply stored | `RECEIPT-P0-004` | covered |
| Receipt replay is idempotent | `RECEIPT-P1-005` | covered |
| Relay custody key must be pinned/trusted | `CUSTODY-P0-001` | needs Task 2 closure |
| Crash after commit/before reply is recoverable | `RECOVERY-P1-001` | covered |
| Integrity failure blocks sensitive writes | `RECOVERY-P0-002`, `AUDIT-P0-001` | covered |
| Corrupt records do not silently poison/disappear | `STORAGE-P1-001` | covered |
| Failed schema migration never opens writable service | `MIGRATION-P0-001` | covered |
| Tested bounded quotas/limits | `RESOURCE-P1-001` | covered at behavioral level |
| Core and transport health remain distinct | `HEALTH-P1-001` | covered |
| Old HPKE keys retained while needed | `KEY-P0-001` | covered |
| Administrative endpoint recovery is discontinuous | `KEY-P0-002` | covered |
| Lost relay custody key creates new installation identity | `RELAY-P0-001` | covered |
| Upgrade/rollback preserves pending mail or fails closed | `UPGRADE-P1-001` | covered |

## Current accounting

The original hostile set contains 21 cases. The coverage-gap supplement adds 15 more, for 36 review cases total. Seven original cases intentionally remain blocked on exact Task 2 normative choices rather than inventing wire semantics in the review lane.

The largest remaining gap is not another design topic: it is **machine-executable exact-byte interoperability evidence** after Work freezes the Task 2 schemas/profile. At that point this review lane should translate the seven closure-dependent cases plus the strict P-256 and pairing cases into concrete cross-language vectors and compare Node, Android, and Host behavior against the same bytes.
