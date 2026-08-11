# BBA protocol specification

This document gives the normative rules for BBA protocol version `bba.epoch.v1`.
The word `must` identifies a required rule.

## 1. Purpose

BBA measures two capabilities:

- A model makes an executable benchmark.
- A model solves an executable benchmark.

The same model cohort acts in both roles.
BBA uses separate sessions for the two roles.

The main public result is a creator-by-solver matrix.
BBA also publishes creator ranks, solver ranks, candidate status, and immutable evidence.

## 2. Terms

**Epoch** means one frozen evaluation run.

**Creator** means a model configuration that makes a candidate package.

**Solver** means a model configuration that receives only a solver bundle.

**Candidate** means one benchmark package from one creator round.

**Snapshot** means one immutable copy of a candidate.

**Cell** means one solver run against one candidate.

**Canonical benchmark** means a candidate that has an approved and signed human review.

**Public evaluator** means the frozen rules that validate, classify, and rank public candidates.

**Holdout** means sealed audit evidence that the public evaluator cannot inspect before public closure.

## 3. Epoch manifest

The controller must freeze the manifest before the first creator run.
The manifest must contain these items:

- The protocol and schema versions
- The epoch ID
- The Google Cloud project and location
- The model cohort
- The public seed
- The creator and solver prompt digests
- The evaluator version
- The resource limits
- The decision limits
- The sandbox capabilities
- The hidden evidence commitments

The cohort must contain at least four model configurations.
The cohort must contain at least three model families.
Each model identity must contain the publisher, model ID, family, and reasoning level.
Provider-qualified configurations are different identities.

BBA accepts only serverless Vertex AI model IDs.
BBA must reject a deployed endpoint resource.
BBA must reject a direct HTTP model URL.

The hidden commitments must contain exactly these names:

- `hidden_solver_panel`
- `hidden_seeds`
- `audit_policy`

Each commitment must be a lowercase SHA-256 digest.

## 4. Model execution

BBA must use Google Python ADK for creator and solver execution.
BBA must make a new ADK session for each invocation.
BBA must not transfer conversation state between cells.

Each invocation trace must contain these items:

- ADK version
- Frozen model identity
- Role
- Session and invocation IDs
- Model-call count
- Tool names
- Token counts
- Event digests
- Start and finish times
- Final status

The public trace must not contain prompts or tool arguments.
The model response must contain token-use data.
BBA must reject a production response that does not contain this data.

## 5. Creator rounds

An epoch must have three creator rounds.

Round 0 is the blind round.
The creator receives no prior candidate feedback.

Rounds 1 and 2 are repair rounds.
The creator receives only public validation evidence and public solver failures for its prior snapshot.
The creator must not receive hidden evidence.

The controller must store each repair as a new snapshot.
The controller must link each repair to its parent snapshot.
The controller must not change an old snapshot.

## 6. Candidate package

A candidate must contain these root files:

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
```

A candidate must contain this public directory:

```text
solver_bundle/
  SOLVER_MANIFEST.json
  items_private_sample.jsonl
  README.md or solver_packet.md
```

The candidate can contain other public assets in `solver_bundle/`.
The public bundle must not contain private gold, an answer map, a verifier, a scorer, or hidden audit data.

The generator must accept this interface:

```bash
python generator.py --sample-count 30 --seed SEED --out-dir .
```

The verifier must accept this interface:

```bash
python verifier.py \
  --items solver_bundle/items_private_sample.jsonl \
  --gold gold_private_sample.jsonl
```

The scorer must accept this interface:

```bash
python scorer.py \
  --gold gold_private_sample.jsonl \
  --predictions predictions.jsonl \
  --out score_report.json
```

Each gold row and prediction row must contain exactly `id` and `answer`.
The score report must use schema version 2.
It must contain `total`, `correct`, and `accuracy`.

The lock file must use exact package versions.
It must not contain a URL or a source-control dependency.

## 7. Sandbox

BBA must treat creator code as untrusted code.
BBA must use an approved operating-system boundary for each command.
BBA must stop if the boundary is not available.

The sandbox must have these properties:

- No network access
- No controller credentials
- No host file-system access
- A temporary home directory
- An ephemeral writable workspace
- CPU, memory, process, and time limits

The sandbox must not contain another candidate, private audit data, or a private controller file.

## 8. Mechanical validation

Mechanical validation makes a candidate eligible for solver tests.
Mechanical validation does not prove that the candidate is useful or fair.

The controller must do these checks:

1. Check that all package entries are regular files or directories.
2. Reject a symbolic link, hard link, or special file.
3. Check the file-count and byte limits.
4. Check all required files.
5. Check the exact dependency pins.
6. Check that the specification names a capability.
7. Regenerate the package in a clean directory.
8. Generate the same seed two times.
9. Require identical payloads for the two same-seed runs.
10. Generate one designated different seed.
11. Require a different payload for the different seed.
12. Require the clean payload to equal the frozen payload.
13. Check the JSON Lines schemas and item IDs.
14. Check the public bundle for answer leakage.
15. Require private gold to score 30 out of 30.
16. Require independent wrong answers to score 0 out of 30.
17. Require the candidate scorer and controller scorer to agree.

If one check fails, the candidate is invalid.
The controller must store the failed validation record.

## 9. Solver matrix

The solver must receive only a fresh copy of `solver_bundle/`.
The solver must submit one answer for every item.
The solver must use the `submit_predictions` tool.
Plain solver text is not a submission.

The default protocol uses three repetitions for each cell.
The controller must store item-level correctness evidence for a successful cell.

A cell can have one of these states:

| State | Meaning |
| --- | --- |
| `success` | Inference, parsing, completeness, and scoring succeeded. |
| `timeout` | The solver exceeded the time limit. |
| `provider_error` | The model provider returned an error. |
| `partial_predictions` | The solver did not return all required answers. |
| `parse_error` | The solver output did not match the contract. |
| `scorer_error` | The candidate scorer failed or disagreed with the controller. |
| `invalid_bundle` | The public solver bundle was not valid. |
| `not_run` | The controller did not start the cell. |

Only `success` can contain a numeric score.
A failed cell is not a zero score.
An incomplete solver panel cannot produce a creator rank.

## 10. Candidate status

The controller applies these public status rules:

| Status | Rule |
| --- | --- |
| `too_easy` | One or more solvers have a median accuracy of 0.50 or more. |
| `solvability_audit` | All successful solvers have an accuracy of zero. |
| `awaiting_review` | The candidate is hard and has no approved review. |
| `frontier_challenge` | The review is approved, but solver outcomes do not differ. |
| `active` | The review is approved, all solver medians are below 0.50, and solver outcomes differ. |
| `invalid` | Mechanical validation failed. |
| `incomplete` | The required solver panel is incomplete. |
| `historical` | The snapshot is retained but is not a current candidate. |

## 11. Ranks

BBA must publish a Round 0 creator rank and a final-round creator rank.
BBA must publish adaptation gain separately.

BBA ranks active creator rows by these keys:

1. Lowest best-solver median accuracy
2. Lowest panel median accuracy

Exact ties must remain tied.

BBA ranks solvers by equal-weight macro-average accuracy across active canonical rows.
BBA must calculate item-level bootstrap confidence intervals.
BBA must not compare aggregate ranks from different benchmark sets without a bridging study.

## 12. Human review and promotion

Mechanical success does not make a benchmark canonical.
An independent human reviewer must approve the benchmark.

The reviewer must check these properties:

- The package measures the named capability.
- A person can solve it from the public material.
- The oracle and scorer are consistent.
- The package does not depend on arbitrary obscurity.
- The package is useful as an evaluation.

The controller selects six of the 30 items after the package freeze.
The reviewer must reconstruct all six answers from public material.
An approval must fail if one reconstructed answer is incorrect.

The promotion record must contain the reviewer ID, candidate digest, evidence digests, decision, limitations, time, key ID, and signature.
The registry must append the record.
The registry must not change an old record.

## 13. Public closure

The controller must freeze the public audit population before public closure.
The controller must not include hidden evidence in the public evaluation record.

The public record must contain these items:

- The complete matrix
- Candidate status
- Blind and final creator ranks
- Adaptation gain
- Solver ranks
- Source digests
- Closure time

## 14. Holdout audit

The controller must run the holdout audit only after public closure.
The revealed hidden material must match all frozen commitments.

The audit must use these two targets:

- The composite target combines declared shared evidence and hidden evidence.
- The hidden-only target excludes the public score components.

The audit must report these values:

- Spearman agreement
- Global pairwise accuracy
- Local pairwise accuracy for the public shortlist
- Gap-stratified pairwise accuracy
- Matched-defect sensitivity
- Top-quartile regret
- Utility recovery
- Set recovery

The combined `bbb_v2_convenience` value is a summary.
The component values are authoritative.

The audit must test matched damage pairs.
Damage categories include a corrupt key, duplicate items, truncation, answer leakage, and a no-op generator.
The audit must also include a profile that optimizes only for the public panel.

If the audit misses one frozen decision limit, the epoch evaluator is `unvalidated`.
BBA must keep the public record.
BBA must retire the revealed holdout.
BBA must not use that holdout in a later epoch.

## 15. Evidence rules

BBA must use canonical JSON for evidence digests.
BBA must store each manifest, snapshot, and evidence record exactly once.
BBA must refuse to overwrite immutable evidence.

Each candidate snapshot ID must bind the creator, round, and package digest.
Each solver cell must bind the epoch, candidate, solver, repetition, and budget.
Each registry record must contain the digest of the prior registry record.

An implementation conforms to this protocol only if it passes the deterministic end-to-end test and all security boundary tests.
