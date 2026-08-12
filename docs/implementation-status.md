# BBA implementation status

This document records the known incomplete work in BBA.
It started from BBA version `0.7.0` and protocol `bba.epoch.v3`.
The [protocol specification](protocol.md) remains the normative source.
The [completion plan](implementation-plan.md) gives the implementation order and required tests.

## Current working scope

BBA has a working local public-tournament flow.
The current implementation can do this work:

1. Create an epoch from the BBA-owned Google Cloud model catalog.
2. Run creator models with Google Python ADK.
3. Freeze all benchmark designs in one round.
4. Select the round seed after all designs freeze.
5. Generate and freeze one evaluation instance from each design.
6. Validate each instance in the local sandbox.
7. Run the public solver panel.
8. Save local evidence and resume incomplete work.
9. Record human reviews.
10. Calculate public ranks and holdout-audit metrics.

The local test suite verifies this flow with deterministic fixture models.
It does not verify a paid production epoch on Vertex AI.

## Completion labels

This document uses these labels:

| Label | Meaning |
| --- | --- |
| `Partial` | Some code exists, but the protocol requirement is not complete. |
| `Missing` | BBA does not perform the required work. |
| `Unverified` | The code exists, but production evidence does not exist. |
| `Decision required` | The written rule and the implementation do not agree. |

## Required protocol work

### 1. Sealed audit execution

**Status: `Missing`**

BBA creates hidden seeds and hidden configuration commitments.
BBA can also calculate the BenchBenchBench metric vector.
However, BBA does not run the hidden experiment.
The operator must currently supply the public, composite, and hidden-only score files.

BBA must add an audit runner that does this work after public closure:

1. Open and verify the committed hidden material.
2. Generate fresh instances from the hidden generator seeds.
3. Run the sealed solver panel.
4. Create matched damage variants.
5. Test corrupt keys, duplicate items, truncation, answer leakage, and no-op generators.
6. Test a profile that optimizes only for the public panel.
7. Derive the composite and hidden-only targets from stored evidence.
8. Calculate and publish the complete audit metric vector.
9. Mark the evaluator as `validated` or `unvalidated`.
10. Retire the revealed holdout.

This work is complete when the operator does not prepare audit score files by hand.

### 2. Independent hidden solver panel

**Status: `Partial`**

The private holdout plan contains a hidden solver configuration.
The configuration currently repeats the public cohort and adds a secret scaffold seed.
No production code uses this configuration.

The hidden panel must use configurations that creators could not optimize against.
It should use later model versions, different model families, or different agent scaffolds when these options are available.

This work is complete when each hidden solver run is bound to its hidden configuration, seed, budget, predictions, and trace.

### 3. Complete solver evidence

**Status: `Partial`**

BBA stores a prediction digest, a score summary, and item-level correctness.
It deletes the prediction file and scorer report when the temporary solver workspace closes.

BBA must preserve these immutable files for each successful cell:

- Exact submitted predictions
- Candidate scorer report
- Controller score report
- Command result and relevant diagnostics
- Digests that bind the files to the cell record

This work is complete when an independent process can replay the score without a new model call.

### 4. Retry rules for solver failures

**Status: `Partial`**

A timeout, provider error, parse error, partial submission, or scorer error becomes a terminal solver-cell record.
The current resume command does not retry that cell.
This behavior does not agree with the operations guide, which tells the operator to correct a provider fault and run the command again.

BBA must define and implement one retry policy.
The policy must preserve every attempt and select one declared attempt for the public matrix.
It must not convert a failed attempt to a zero score.

This work is complete when a safe retry cannot overwrite old evidence or change the matrix without a recorded selection rule.

### 5. Incomplete-panel ranking rule

**Status: `Decision required`**

The protocol says that an incomplete solver panel cannot produce a creator rank.
The protocol also puts `incomplete` in the creator status order.
The implementation follows the second rule and assigns a rank to an incomplete row.

The protocol must select one rule.
The implementation and tests must then enforce that rule.

### 6. Human promotion gate

**Status: `Partial`**

BBA checks the six reconstructed answers and stores a signed promotion record.
It does not store a structured review of all construct-validity requirements.
It can also create an approved record without first checking all candidate eligibility conditions in the promotion method.

Approval must require these conditions:

- Passed mechanical validation
- Complete required solver panel
- Eligible final-round candidate status
- Six correct reconstructed answers
- Named capability is valid
- Public materials are sufficient for a person to solve the task
- Oracle and scorer are consistent
- No arbitrary obscurity
- Useful evaluation purpose
- Second-reviewer escalation when the first review finds a discrepancy

The current signature is an HMAC made with a shared secret.
BBA must either document this trust model or use a public-key signature that other parties can verify without the reviewer secret.

### 7. Dependency isolation

**Status: `Partial`**

BBA checks that `requirements.lock` contains exact versions.
BBA does not install those dependencies into an isolated locked environment.
Generated code therefore cannot use a declared third-party package in the complete intended way.

BBA must create a local, network-controlled environment from an approved dependency source.
The frozen dependency artifacts and their digests must become part of the evidence.
Validation and replay must use the same environment.

### 8. Sandbox conformance

**Status: `Partial`**

BBA uses macOS Seatbelt and stops when the sandbox is not available.
The current security tests check one host-file read.
They do not prove every sandbox statement in the protocol.

The security suite must test these boundaries:

- No network access
- No Application Default Credentials access
- No access to the evidence root
- No access to hidden audit files
- No access to another candidate
- No access to unrelated host file contents or metadata
- Temporary home and temporary directory
- Process limit
- Memory limit
- CPU limit
- Wall-clock timeout
- Child-process termination after timeout

The current sandbox does not set an explicit CPU resource limit.
It can also continue when some supplementary process or memory limits are not available.
The implementation and protocol claim must agree.

### 9. Evaluator version binding

**Status: `Partial`**

The manifest contains a fixed evaluator-version string.
The string does not prove which source code, dependency versions, prompts, validation rules, or scoring rules produced the public result.

BBA must bind the evaluator version to a reproducible source and dependency digest.
An evaluator change must create a new evaluator version and a new sealed audit target.

### 10. Holdout retirement across epochs

**Status: `Partial`**

One audit record says that its holdout is retired.
BBA does not have a global registry that prevents another epoch from using the same revealed holdout material.

BBA must store a local append-only registry of holdout commitments and retirement state.
Epoch creation and audit execution must reject a reused retired commitment.

## Production verification work

### 11. Live Vertex AI model verification

**Status: `Unverified`**

All catalog routes resolve to local ADK model classes.
This does not prove that every model is available to the configured project at run time.

A production smoke test must verify these properties for every catalog model:

- The project can access the model.
- The model accepts serverless inference in the `global` location.
- Model Garden terms are accepted.
- Quota is available.
- Function calling works with the BBA tool contract.
- ADK receives token-use metadata.
- The model identity in the response agrees with the frozen identity.

The smoke test must make no deployment.
It must have a small fixed request and token limit.

### 12. Frozen model behavior settings

**Status: `Partial`**

The model catalog records `provider-default` reasoning behavior.
BBA does not apply one explicit reasoning configuration for every model family.
Provider defaults can change.

BBA must freeze and apply every supported behavior setting that can affect a run.
When a provider does not support one setting, the manifest and trace must record that limitation.

### 13. Full production epoch

**Status: `Unverified`**

The end-to-end tests use local fixture creators and solvers.
BBA has not completed one full paid epoch with all catalog models.

The production acceptance run must verify these outputs:

- Three complete creator rounds
- One frozen seed for each round
- One immutable instance for each valid design
- Complete public solver matrix
- Human review records
- Public closure record
- Automatic sealed audit record
- Successful restart from at least one controlled interruption

## Operational improvements

These items improve safe operation.
They are not substitutes for the required protocol work.

### 14. Cost estimate and hard run limit

**Status: `Partial`**

BBA records per-invocation token use and has per-run budgets.
It does not calculate a complete epoch cost estimate before inference.
It also does not enforce one total epoch cost limit.

BBA should show the planned invocation count, estimated token range, and configured hard limit before a paid run.

### 15. Bounded concurrency

**Status: `Missing`**

The public tournament runs work in sequence.
A complete epoch can contain many independent solver cells.

BBA can add bounded local concurrency after evidence locking, provider quota control, deterministic scheduling, and resume behavior are tested.
Concurrency must not change cell identity or public results.

### 16. Continuous integration

**Status: `Missing`**

The repository does not contain a continuous-integration workflow.
A workflow should run compilation, the local test suite, diff checks, and supported sandbox checks on each change.
Live Vertex tests must remain a separate, explicit, paid job.

## Completion order

Use this order:

1. Preserve complete solver evidence.
2. Define retry and incomplete-panel rules.
3. Tighten the human promotion gate.
4. Complete dependency and sandbox conformance.
5. Bind evaluator versions and holdout retirement.
6. Implement the sealed audit runner and independent hidden panel.
7. Run live Vertex smoke tests.
8. Run one full production acceptance epoch.
9. Add cost controls, bounded concurrency, and continuous integration.

## Definition of complete

BBA is complete only when all these statements are true:

- The implementation satisfies every `must` rule in the protocol specification.
- The operator does not prepare public or hidden audit scores by hand.
- Every published score can be replayed from immutable evidence.
- Failed inference attempts follow one frozen retry and selection rule.
- No benchmark becomes canonical without the complete human-review gate.
- All sandbox claims have executable security tests.
- Every catalog model passes a small live serverless Vertex AI test.
- One full production epoch passes the public tournament, review, resume, and sealed audit acceptance tests.
