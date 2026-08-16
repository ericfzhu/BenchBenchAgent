# BBA implementation status

This document records the implementation and verification state for BBA
version `0.13.0` and protocol `bba.epoch.v8`. The
[protocol specification](protocol.md) is normative. The
[production acceptance record](production-acceptance.md) remains the release
gate for a paid epoch.

## Status labels

| Label | Meaning |
| --- | --- |
| `Implemented` | The code and deterministic local tests exist. |
| `Partial` | Some required code or target-host proof is incomplete. |
| `External proof required` | Local implementation exists, but paid or independent evidence does not. |

## Implementation table

| Item | Status | Current evidence |
| --- | --- | --- |
| Sealed audit execution | `Implemented` | `SealedAuditRunner` opens committed material after public closure, creates fresh instances, runs the hidden panel, tests five damage classes, derives both targets, publishes the metric vector, and retires the holdout. |
| Independent hidden solver panel | `Implemented` | Epoch setup commits distinct sealed-scaffold identities. Hidden cells use the immutable attempt and trace contract used by public cells. |
| Complete solver evidence | `Implemented` | Each success preserves locked predictions, a structured item debrief, candidate and controller scorer reports, command diagnostics, digests, and item-level results. Offline replay performs no inference. |
| Retry and resume rules | `Implemented` | Timeout and provider failures create immutable solver attempts. Creator retries receive distinct inference reservations, so an interrupted or failed creator call cannot reuse a prior budget reservation. |
| Incomplete-panel ranking | `Implemented` | An incomplete or invalid row remains visible with `rank: null` and does not enter solver aggregates. |
| Human promotion gate | `Implemented` | Promotion requires a typed, digest-bound solvability certificate and a separate signed human adjudication. New human inputs are rejected as soon as the public audit population is frozen. |
| Public-close recovery | `Implemented` | Re-running public close idempotently republishes every approved promotion to the canonical registry, including recovery after a process stops between evaluation publication and registry append. |
| Dependency isolation | `Implemented` | BBA accepts standard-library packages or exact hashed wheels from the local catalog. The current wheel catalog is empty, so candidate packages are standard-library-only. |
| Sandbox conformance | `Partial` | Ubuntu uses Bubblewrap and macOS uses Seatbelt. Public work, audit-population generation, and sealed audit require an available backend matching the frozen epoch manifest. The target Ubuntu security suite still must pass without skips. |
| Evaluator version binding | `Implemented` | The manifest binds facade and implementation modules, GCP routing code, prompts, protocol, model and price catalogs, Python, and installed ADK/Auth/Vertex/LiteLLM/Pydantic distributions. |
| Holdout retirement | `Implemented` | A cross-epoch append-only registry records committed, opened, and retired states and rejects reuse. |
| Live Vertex verification | `External proof required` | Paid preflight checks every frozen route, tool contract, usage metadata, global routing, and returned model metadata when available. No paid record is committed to this repository. |
| Frozen model settings | `Implemented` | The catalog freezes model routes, temperature, top-p, scaffold identity, and supported behavior metadata. |
| Full production epoch | `External proof required` | The deterministic fixture lifecycle exists. A paid all-catalog epoch, controlled live interruptions, and independent reviews have not run. |
| Cost estimate and hard limits | `Implemented` | A versioned price catalog covers every frozen route. Preflight checks a retry-inclusive estimate. SQLite reserves and reconciles calls, tokens, and a conservative USD estimate with a frozen 2× safety factor and hard epoch ceiling. Google Cloud budgets remain an independent operator control. |
| Bounded concurrency | `Implemented` | `BoundedScheduler` enforces global and per-publisher limits while retaining deterministic work IDs and ordered barriers. |
| Continuous integration | `Removed` | The project intentionally uses the target operator host for package, sandbox, unit, and paid preflight checks. The development portal can launch the local suite. |
| Local development portal | `Implemented` | The localhost portal presents readiness checks, serialized diagnostics, a phase-aware epoch workflow, usage and cost status, failed work, candidate review, rankings, and observability. Review forms become read-only at audit freeze. |
| ADK observability | `Implemented` | A Google ADK plugin records content-free lifecycle, token, tool, latency, model-version, and error metadata. |
| OpenTelemetry tracing | `Implemented` | Optional loopback-only OTLP export uses an allowlist that removes prompts, responses, arguments, results, descriptions, events, links, and exception text. |

## Local verification coverage

The local suite covers:

- Protocol and manifest validation
- Three creator rounds and post-design seed barriers
- Immutable design and instance chains
- Public solver matrices and rankings
- Creator and solver retry accounting
- Offline score replay
- Ed25519 review gates and the review-freeze boundary
- Crash-safe public-close publication
- Local dependency identity
- Evaluator and holdout registries
- Automatic sealed audit and damage sensitivity
- Sandbox backend binding
- Price coverage and conservative USD limits
- ADK sessions, tools, budgets, settings, and redacted traces
- Interruption and resume
- Preflight behavior with deterministic models
- Local portal security, diagnostics, workflow rendering, and job serialization

A skipped security test is not sandbox proof. The Linux conformance record must
come from the Ubuntu target host with Bubblewrap available.

## Remaining acceptance work

The following requires the configured Google Cloud project or independent human
participants:

1. Run the complete local suite on the target Ubuntu host without security-test skips.
2. Run `bba epoch preflight` and confirm access, accepted terms, quota, function calling, usage metadata, and routing for every catalog model.
3. Record provider model-version metadata where Vertex AI supplies it.
4. Run one paid three-round epoch with the complete catalog.
5. Stop and resume live creator, public-solver, and hidden-solver work.
6. Obtain independent solvability certificates and signed human decisions.
7. Freeze and publish the public evaluation, run the sealed audit, and replay every successful live attempt.
8. Complete `docs/production-acceptance.md` with the resulting evidence digests.

## Completion rule

BBA is not production-verified until the target-host security run, live
preflight, full paid epoch, independent review, sealed audit, and replay checks
all pass. Deterministic fixture success establishes implementation behavior; it
does not establish current provider availability, quota, billing, or construct
validity.
