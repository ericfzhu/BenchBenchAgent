# BBA implementation status

This document records the implementation and verification state.
It applies to BBA version `0.10.0` and protocol `bba.epoch.v6`.
The [protocol specification](protocol.md) is the normative source.
The [completion plan](implementation-plan.md) gives the work order and acceptance gates.

## Status labels

| Label | Meaning |
| --- | --- |
| `Implemented` | The code and deterministic local tests exist. |
| `Partial` | Some required code or proof is not complete. |
| `External proof required` | Local implementation exists, but paid or independent evidence does not exist. |

## Implementation table

| Item | Status | Current evidence |
| --- | --- | --- |
| 1. Sealed audit execution | `Implemented` | `SealedAuditRunner` opens committed material after closure, creates fresh instances, runs hidden solvers, tests five damage classes, derives both targets, publishes the metric vector, and retires the holdout. No audit score file is accepted. |
| 2. Independent hidden solver panel | `Implemented` | Epoch setup commits distinct sealed-scaffold identities. Hidden cells use the same immutable attempt and trace contract as public cells. A later model family can replace a scaffold identity in a new catalog version. |
| 3. Complete solver evidence | `Implemented` | Each success preserves locked predictions, a structured item debrief, two scorer reports, command diagnostics, file digests, and item results. Later creator rounds receive bounded correctness-annotated public debrief feedback. `bba evidence replay-cell` verifies the debrief and replays a score without inference. |
| 4. Retry rules | `Implemented` | The protocol retries only timeout and provider error. It permits three immutable attempts and selects the first success. Fault-injection tests cover two failures followed by success. |
| 5. Incomplete-panel ranking | `Implemented` | An incomplete or invalid row stays in the matrix and has `rank: null`. It does not enter solver aggregates. |
| 6. Human promotion gate | `Implemented` | Approval checks mechanical validity, panel completeness, final-round eligibility, six answers, six structured findings, and escalation rules. Records use Ed25519. The trust registry stores public keys only. |
| 7. Dependency isolation | `Implemented` | BBA accepts standard-library packages or exact hashed wheels from the local catalog. Installation uses local wheels with no dependency resolution. Validation stores environment digests. |
| 8. Sandbox conformance | `Partial` | Ubuntu uses Bubblewrap namespaces. macOS uses Seatbelt. Both backends deny network and unrelated host paths. They use temporary home and temporary directories. CPU, memory, process, file, and wall limits fail closed. The same security suite tests both backends. The Ubuntu CI job must pass before this item can be `Implemented`. |
| 9. Evaluator version binding | `Implemented` | The manifest stores a root digest and component digests for bound source, prompts, protocol, catalog, runtime, and installed controller packages. |
| 10. Holdout retirement | `Implemented` | A cross-epoch append-only registry records committed, opened, and retired states. It rejects reused or retired commitments. |
| 11. Live Vertex verification | `External proof required` | `bba epoch preflight` checks all routes with a small ADK tool call, usage metadata, global routing, behavior settings, and returned model metadata when available. No paid record exists in this repository. |
| 12. Frozen model settings | `Implemented` | The catalog freezes temperature, top-p, scaffold identity, and a structured unsupported reasoning field. The ADK request plugin applies supported values and traces them. |
| 13. Full production epoch | `External proof required` | The complete local fixture passes. A paid all-catalog epoch, controlled live interruption, and independent human reviews have not run. |
| 14. Cost estimate and hard limits | `Partial` | BBA reports invocation and token ceilings. SQLite reservations enforce epoch call and token limits across retries and resume. A versioned price catalog fails visibly when an exact published price is absent. Current exact prices are not recorded. |
| 15. Bounded concurrency | `Implemented` | `BoundedScheduler` runs public solver cells with deterministic work IDs, a global limit, and a per-publisher limit. Creator rounds, validation, and the public-to-hidden barrier remain ordered. Resume and retry tests run with this scheduler. |
| 16. Continuous integration | `Implemented` | CI runs compilation, tests, diff checks, package build, and separate Ubuntu and macOS security jobs. A separate manual workflow performs the paid Vertex smoke test with workload identity. |

## Local verification

The local suite covers these flows:

- Protocol version and contract validation
- Three creator rounds and round seed barriers
- Immutable design and instance chains
- Public solver matrix and rankings
- Immutable retry attempts
- Offline score replay
- Ed25519 review gates
- Local dependency environment identity
- Evaluator and holdout registries
- Automatic sealed audit and damage sensitivity
- Public-optimizer selection gap
- ADK sessions, tools, token limits, settings, and traces
- Local interruption and resume
- Preflight behavior with deterministic ADK models
- Bounded scheduler and budget reservations

The Ubuntu security job installs Bubblewrap and requires the complete security suite to run.
The macOS security job requires Seatbelt.
A skipped security job is not sandbox proof.
The Linux backend cannot run on the macOS development host.
Use the Ubuntu CI result or an Ubuntu host as the Linux conformance record.

## Remaining acceptance work

The following work cannot be completed with fixture models:

1. Run `bba epoch preflight` with the configured Google Cloud project.
2. Confirm that every catalog model has access, accepted terms, quota, tool use, and usage metadata.
3. Record returned model-version metadata where Vertex AI supplies it.
4. Run one paid three-round epoch with all catalog models.
5. Stop and resume the live epoch during creator, public solver, and hidden solver work.
6. Obtain independent signed human reviews for promotion candidates.
7. Run the automatic sealed audit and replay every successful live score.
8. Publish the production acceptance report and evidence digests.

## Completion rule

BBA is not production-verified until the live preflight and full production acceptance run pass.
Fixture success proves deterministic implementation behavior.
It does not prove current model availability, quota, cost, or independent construct validity.
