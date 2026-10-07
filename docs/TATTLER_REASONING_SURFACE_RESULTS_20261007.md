# Tattler reasoning-surface results — 2026-10-07

Status: OBSERVATION NOTE / TRANSPORT-COGNITION SEPARATION

## Shared experiment result

On 2026-10-07, the same repository stress-test prompt was run through three ChatGPT surfaces while WorkLaptop was instrumented with Tattler plus a companion Codex process/network tracer.

Observed controlled windows:

- Desktop Chat, GPT-5.6 Sol High: **0 MXC launches** and **2 new established Codex TLS connections** in the companion tracer.
- ChatGPT Desktop Work, Ultra: **59 MXC launches** and **73 new established Codex TLS connections** using the same companion-tracer definitions.
- Firefox cloud Work, Max: browser-side traffic was observable locally, but the provider's server-side worker topology was not.

The bounded conclusion is that Desktop Work used materially different local orchestration from ordinary High Chat in this runtime. It does **not** establish that sockets or MXC processes equal agents, that connection fanout grants a reasoning tier, or that a client can promote High into Ultra/Max by imitating transport behavior.

Canonical detailed evidence is being preserved in `thebrazenbeard/tattler` PR #7 and the reasoning interpretation in `thebrazenbeard/rezon` PR #103.


## Why VeraMesh needs this result

VeraMesh explicitly separates transport, device identity, routing, execution lanes, and authority. The Tattler experiment adds a concrete reason to keep cognition separate as well.

A single reasoning surface may create many local processes and connections, while a browser surface can hide most orchestration remotely. Therefore VeraMesh must not derive cognitive topology from transport topology.

```text
VeraPort lane != reasoning agent
multiplexed stream != model worker
connection fanout != cognition fanout
transport path != reasoning tier
```

If a VeraMesh request carries product-surface or reasoning metadata, that metadata must come from an authenticated/explicit higher-level request or receipt. It is not inferred from stream count, route class, relay hops, or packet/connection observations.

## Tattler interoperability

Tattler can be useful beside VeraMesh for host-observation evidence. Such evidence should remain a separate telemetry layer in receipts, preserving whether each field is transport-observed, process-observed, or application-asserted.
