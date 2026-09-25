# VeraPort source-transfer record — 2026-09-19

Status: SOURCE RESEARCH / VERA-MESH IS CURRENT WRITE TARGET

Patrick originally supplied Project Runner, Chat Communication Bus, Rezon, and Vera Synology as supporting source repositories. Later on 2026-09-19 he clarified that VeraRelay/Synology may be updated if needed and that VeraRelay may be replaced/absorbed by VeraMesh; the goal is maximum practical synchrony, not preservation of a particular component boundary.

This branch still modifies only `thebrazenbeard/vera-mesh`. The clarification removes any architectural assumption that VeraRelay must remain untouched or must stay in the live data path.

## Project Runner

Source: `thebrazenbeard/project-runner@bc05812b560b4fcde3a362e72fba04c626cafac8`.

Transferred semantics: elastic parallelism bounded by authority/collision controls; capability narrowing; collision partitioning; expiring fenced leases.

Exact blobs read:
- `runner/leases.py` — `eeb34650b6a2f6e990917a6e6c9075c18800ccab`
- `runner/collisions.py` — `ab55770cc39128f14bc6ac9ac1586ef9d857fbc2`
- `runner/capabilities.py` — `8740b9699f25f1a64da74c3ad5d10462eb47d25c`

## Chat Communication Bus

Source: `thebrazenbeard/chat-communication-bus@aeab0f04fc9b4bd7c2945c9a53011c53fac809b4`.

Transferred semantics: routing/priority is not authority; idempotency is explicit; relay acceptance is distinct from downstream completion; ambiguous mutation requires reconciliation.

Exact blobs read:
- `src/radar/routing.py` — `b871234677c839ff00b36ea77a498e4903444558`
- `src/chat_bus/ledger.py` — `5353659edbb0c2940abc009c0ac51dc9f02dcffb`

## Rezon

Source branch observed: `thebrazenbeard/rezon:work/rezon-kernel-v0-r10-task-envelope-projection`.

Transferred semantics: bounded task-envelope projection; independent workers do not silently inherit ambient authority/context; execution authority remains a separate proposition.

Exact blobs read:
- `README.md` — `f8495b73ccdfeae43514384dc3188df335dccbe9`
- `src/rezon/envelopes.py` — `a78465ef551818d16d86e91165bb4268d86ea9fd`
- `tests/test_r10_task_envelope_projection.py` — `4585f2b454cb65f1d91274d1fb63f1d83393027b`

## Vera Synology

Source branch observed: `thebrazenbeard/vera-synology:feature/mesh-ds216-target-first-repair-v1`.

Transferred semantics: fail-closed durable local state; atomic replacement/integrity checking; package lifecycle/readiness is distinct from protocol implementation and runtime acceptance.

Exact blobs read:
- `SOURCE_MANIFEST.json` — `03fe69d4347e507b0760614e2b1c26344220a928`
- `payload/bin/veramesh_state.py` — `f4969433657a6b88973f91e33753027d72811025`

The Synology implementation is now an eligible future write/migration target when a VeraMesh edge/relay integration step actually requires it. No Synology write occurs in this branch.

## Non-transfer rule

Source history does not itself grant merge, deployment, credential, network, workstation, or OS authority. Any future cross-repository change must still be exact-subject and collision-safe.
