# Synology / VeraMesh edge integration gate

Date: 2026-09-19
Status: WAITING_ON_SYNOLOGY_RUNTIME_QUALIFICATION

## Exact inspected predecessor

Repository: `thebrazenbeard/vera-synology`
Branch: `feature/mesh-ds216-target-first-repair-v1`
Exact head observed: `cd0496ede55139c9309ffaff74cf9af3e781765a`

Relevant exact blobs:
- `payload/bin/veramesh_lifecycle.py` — `b3f57ac7550182ff8a27fbd5b364d8cd86fbed36`
- `payload/bin/veramesh_start_readiness.py` — `444cd4497ebdcd260a5e7408c47a720ccebb7597`
- `spk/INFO` — `d910bc392d321aaeaa153f4372410dfe8608148c`

## Current evidence boundary

The package metadata describes the candidate as a first-install scaffold and explicitly states that Mesh transport is not implemented.

The lifecycle status surface likewise retains `BLOCKED_MESH_NOT_IMPLEMENTED` semantics.

The bounded start-readiness successor deliberately leaves `TARGET_POLICY = None` until a DS216-specific timing/error policy is qualified and bound. Its own comments state that a later target qualification must materialize that policy and rebind the artifact/reviews.

Therefore the inspected Synology candidate is not a justified live VeraPort `EDGE_STREAM` runtime target yet.

## Integration decision

Do not inject the VeraPort live edge proxy into this exact Synology package head.

Doing so would conflate:
- package/source presence;
- lifecycle/readiness qualification;
- live Mesh transport implementation;
- VeraPort edge capability;
- deployed/runtime acceptance.

Instead:

1. keep the direct Lappy `DIRECT_STREAM` implementation independent;
2. keep VeraPort's `LiveEdgeProxy` / `DurableRelayFallback` interfaces transport-neutral in `vera-mesh`;
3. finish or supersede the Synology package readiness/runtime qualification;
4. only then bind the edge adapter into a fresh exact Synology package subject;
5. qualify live-proxy and durable-fallback paths separately;
6. preserve queue-custody != Lappy-execution semantics.

## Replacement remains allowed

This hold does not require preserving VeraRelay or the current Synology package architecture.

A later implementation may:
- extend VeraRelay;
- replace VeraRelay with a VeraMesh edge daemon;
- package the new edge daemon for Synology;
- use Synology only for durable fallback/rendezvous while direct Lappy sessions handle interactive traffic.

The requirement is invariant preservation and fresh qualification, not component-name preservation.

## Current frontier

`DIRECT_STREAM` development may continue independently.

Synology `EDGE_STREAM` deployment frontier:
`WAITING_ON_SYNOLOGY_RUNTIME_QUALIFICATION`.

No Synology repository write, package build, install, listener, credential, or network effect is performed by this gate.
