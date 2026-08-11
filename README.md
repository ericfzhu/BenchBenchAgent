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

Run all tests using only the standard library:

```bash
python3 tests/run_tests.py
```

Inspect the production sandbox boundary:

```bash
python3 -m bba.cli sandbox-status
```

Validate a candidate. This refuses to run if the secure sandbox is unavailable:

```bash
python3 -m bba.cli verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260811
```

Run the deterministic protocol conformance fixture:

```bash
python3 -m bba.cli demo --out /tmp/bba-conformance
```

The fixture executes a 4x4 cohort over three creator rounds and three solver
repetitions: 12 immutable candidate snapshots and 144 solver cells. Its local
runner accepts only source-marked repository fixtures and is never used by the
production validator. Fixture promotion signatures and model results are not
scientific evidence.

## Python API

The principal interfaces are:

- `ExperimentManifest`: frozen epoch protocol and hidden commitments;
- `TournamentController`: creation, validation, solver matrix, review, closure,
  and holdout lifecycle;
- `CreatorBackend` / `SolverBackend`: provider integration boundaries;
- `PackageValidator` / `SecureSandbox`: fail-closed package execution;
- `PromotionRegistry`: signed append-only canonical decisions; and
- `audit_evaluator`: decision-level BenchBenchBench metric vector.

Raw evidence lives under `epochs/<epoch_id>/`. Registry entries are immutable,
hash-chained files under `registries/`. Corrections create new records; they do
not rewrite historical runs.

## Status

Version 0.2 implements the complete protocol and deterministic end-to-end
conformance harness. Live model providers are intentionally adapter interfaces:
each provider must establish an audited credential and filesystem boundary
before it is eligible for a real epoch. BBA never falls back to an unrestricted
host model call.
