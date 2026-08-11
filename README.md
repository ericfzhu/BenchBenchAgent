# BenchBenchAgent (BBA)

BBA is a two-sided tournament for executable benchmark creation. The same
provider-qualified model cohort acts in two isolated roles:

- **Creators** build complete, reproducible benchmark packages.
- **Solvers** receive only public bundles and try to solve every validated row.

The headline artifact is a creator-by-solver matrix. BBA also publishes blind
and adaptive creator rankings, a solver ranking over active canonical rows, an
append-only benchmark registry, and a BenchBenchBench-style audit of the public
evaluator against evidence it could not inspect.

This design follows Rohit Krishnan's
[BenchBench](https://www.strangeloopcanon.com/p/introducing-benchbench) and its
[methodology](https://github.com/strangeloopcanon/benchbench/blob/main/docs/methodology.md).
The outer evaluator audit follows Ethan Mollick's
[BenchBenchBench](https://github.com/emollick/benchbenchbench).

## Google ADK runtime

BBA uses Google's Python Agent Development Kit **2.6.3** as a required,
exactly pinned runtime dependency. It does not contain a home-grown workflow
fallback.

The responsibility boundary is deliberate:

- ADK `Agent`, `App`, `Runner`, sessions, function tools, plugins, and event
  streams execute creator and solver model turns.
- `TournamentController` owns round scheduling, immutable snapshots, package
  validation, the creator-by-solver matrix, scoring, rankings, promotion, and
  sealed holdout release.
- Creator and solver calls get a new `InMemorySessionService` and isolated ADK
  session for every creator round and solver-cell repetition. No conversational
  state crosses a cell boundary.
- A controller-published ADK trace records the exact ADK version, provider-
  qualified identity, session and invocation IDs, model-call count, tool names,
  token usage, and event digests. Prompts and tool arguments are not copied into
  the trace. Production backends reject providers that omit usage metadata,
  because the frozen cumulative token budget would otherwise be unenforceable.

Creators receive workspace-scoped read/write tools and may run generated Python
only through the credential-free construction sandbox. Solvers receive only
read-only access to the copied `solver_bundle` and must call
`submit_predictions` with one complete prediction set. Prose output is never
silently interpreted as benchmark evidence.

Native Google models can be resolved from a `ModelIdentity` directly. Other
providers remain first-class ADK models by supplying an explicit
`google.adk.models.BaseLlm` implementation or provider adapter for that frozen
identity:

```python
from bba.adk_runtime import build_adk_backends

creator_backends, solver_backends = build_adk_backends(
    manifest,
    model_overrides={
        identity.artifact_id: provider_adk_model,
    },
    construction_sandbox=secure_sandbox,
)
```

Live tournament agents are created by `AdkCreatorBackend` and
`AdkSolverBackend` because their tools and ADK sessions must be bound to one
isolated candidate or solver cell. BBA does not ship a context-free placeholder
agent.

## Protocol

Each epoch freezes:

- at least four model configurations from at least three model families;
- creator and solver prompts, tools, model/provider identities, and budgets;
- three creation rounds and three independent solver repetitions;
- the public evaluator and decision thresholds; and
- SHA-256 commitments to the hidden solver panel, hidden seeds, and audit policy.

Round 0 measures blind creation. Rounds 1 and 2 expose only public validation
and public solver evidence. Every submission is an immutable, content-addressed
snapshot linked to its parent.

Valid packages contain:

```text
README.md
benchmark_spec.json
generator.py
verifier.py
scorer.py
gold_private_sample.jsonl
validation_report.md
failure_modes.md
requirements.lock
solver_bundle/
  SOLVER_MANIFEST.json
  items_private_sample.jsonl
  README.md or solver_packet.md
  ...solver-visible assets
```

The generator CLI is:

```bash
python generator.py --sample-count 30 --seed <seed> --out-dir .
```

The verifier and scorer CLIs follow the BenchBench contracts:

```bash
python verifier.py \
  --items solver_bundle/items_private_sample.jsonl \
  --gold gold_private_sample.jsonl

python scorer.py \
  --gold gold_private_sample.jsonl \
  --predictions predictions.jsonl \
  --out score_report.json
```

Score reports require schema version 2 and exact `total`, `correct`, and
`accuracy` fields. Prediction and gold rows contain exactly `id` and `answer`.

## Validity and isolation

Creator-authored code is untrusted. `bba.runtime.SecureSandbox` has no
unrestricted fallback: if an audited OS boundary is unavailable, validation
fails closed. The macOS backend uses Seatbelt with an ephemeral home, denied
network, allowlisted runtime reads, workspace-only writes, timeouts, and
supplementary resource limits. A container/VM backend can be added behind the
same interface for other platforms.

The controller independently checks:

- real files only, bounded tree size, and no links or special files;
- locked dependencies and a named capability claim;
- clean-directory generation;
- identical same-seed payloads and different designated-seed payloads;
- equality between the frozen payload and clean regeneration;
- exact item/gold ID contracts and absence of public answer mappings;
- 30/30 gold and 0/30 independently generated wrong controls; and
- agreement between creator scoring and controller exact-match scoring.

A timeout, provider error, parse failure, partial output, or scorer error is a
state—not a numeric zero. An incomplete panel cannot rank a creator.

## Classification and promotion

A mechanically valid candidate is:

- **too easy** if any solver's median accuracy is at least 50%;
- sent to **solvability audit** if every solver scores zero;
- **awaiting review** if it is hard but not human-approved;
- a **frontier challenge** if approved but the public panel has no differing
  outcomes; or
- **active** if approved, below the rejection threshold, and discriminative.

Canonical promotion requires a signed independent review. The controller
deterministically chooses six items after package freeze, and an approval is
accepted only when the reviewer reconstructs all six answers from public
materials. Promotion records bind the reviewer, candidate, validation and
solver-cell evidence, limitations, timestamp, key ID, and HMAC signature.

## Sealed evaluator audit

The audit population and public evaluator scores are frozen before public
closure. Only then may the audit authority reveal material whose digests match
the preregistered commitments. The audit compares public rankings with both:

- a composite holdout target retaining declared shared validity components;
- a hidden-only target based on fresh seeds and a sealed solver panel.

It reports Spearman agreement, global and shortlist-local pairwise accuracy,
gap-stratified accuracy, matched-defect sensitivity, top-quartile regret,
utility recovery, and set recovery. The combined BBB-v2 value is convenience
output only. Revealed holdouts are marked retired and cannot be reused.

Controlled package-damage operators cover corrupted keys, duplicate items,
truncation, answer leakage, and no-op generators. Public-panel optimization is
represented as a behavioral audit profile.

## Usage

Create Python 3.10+ environment and install the pinned runtime:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Run all tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Inspect the production sandbox boundary:

```bash
.venv/bin/python -m bba.cli sandbox-status
```

Validate a candidate. This refuses to run if the secure sandbox is unavailable:

```bash
.venv/bin/python -m bba.cli verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260811
```

The test suite includes a deterministic 4x4 protocol conformance fixture over
three creator rounds and three solver repetitions: 12 immutable candidate
snapshots and 144 solver cells. All fixture backends and the fixture-only local
runner live under `tests/`; none are installed with BBA or exposed by its CLI.

## Python API

The principal interfaces are:

- `ExperimentManifest`: frozen epoch protocol and hidden commitments;
- `TournamentController`: creation, validation, solver matrix, review, closure,
  and holdout lifecycle;
- `AdkCreatorBackend` / `AdkSolverBackend`: native ADK execution boundaries;
- `build_adk_backends`: exact-cohort ADK backend construction and model
  adapter binding;
- `PackageValidator` / `SecureSandbox`: fail-closed package execution;
- `PromotionRegistry`: signed append-only canonical decisions; and
- `audit_evaluator`: decision-level BenchBenchBench metric vector.

Raw evidence lives under `epochs/<epoch_id>/`. Registry entries are immutable,
hash-chained files under `registries/`. Corrections create new records; they do
not rewrite historical runs.

## Status

Version 0.3 implements the complete protocol, a deterministic end-to-end
conformance harness, and native Google ADK 2.6.3 creator/solver execution. Each
provider adapter must establish audited credentials, a frozen model identity,
and the required filesystem boundary before it is eligible for a real epoch.
BBA never falls back to an unrestricted host model call or a substitute agent
runtime.
