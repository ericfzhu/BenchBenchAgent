# BenchBenchAgent (BBA)

BenchBenchAgent (BBA) evaluates models that make and solve executable benchmarks.
Each model configuration has two isolated roles:

- A **creator** makes a complete benchmark package.
- A **solver** tries to solve each valid benchmark.

The main result is a creator-by-solver matrix.
BBA publishes separate creator ranks for blind and adaptive results.
BBA publishes solver ranks for active canonical rows.
BBA also publishes an append-only benchmark registry.
BBA audits its public evaluator against hidden evidence.

Rohit Krishnan's [BenchBench](https://www.strangeloopcanon.com/p/introducing-benchbench) and its [method](https://github.com/strangeloopcanon/benchbench/blob/main/docs/methodology.md) define the creator evaluation.
Ethan Mollick's [BenchBenchBench](https://github.com/emollick/benchbenchbench) defines the hidden evaluator audit.

## Google ADK runtime

BBA requires Google Python Agent Development Kit (ADK) version 2.6.3.
BBA does not contain a substitute agent runtime.

ADK and the BBA controller have different functions:

- ADK supplies the `Agent`, `App`, `Runner`, session, function-tool, plugin, and event-stream components.
- `TournamentController` controls the rounds, snapshots, validation, matrix, scores, ranks, promotion, and hidden audit.
- BBA creates a new `InMemorySessionService` and a new ADK session for each model run.
- BBA does not transfer conversation data between cells.

An ADK trace records the ADK version and the GCP-qualified model identity.
The trace also records session IDs, invocation IDs, model-call counts, tool names, token use, and event digests.
The trace contains hashes of events.
The trace does not contain prompts or tool arguments.
`TournamentController` stores each trace with the epoch evidence.

A production backend rejects a model response that does not supply token-use data.
Without token-use data, BBA cannot enforce the cumulative token limit.

Each creator gets tools that can read and write only in its workspace.
Each creator can run Python only in the credential-free construction sandbox.
Each solver can read only its copy of `solver_bundle`.
The solver must use `submit_predictions` to send one complete prediction set.
BBA does not convert solver text into benchmark evidence.

BBA sends all model requests through serverless Vertex AI APIs.
The epoch manifest freezes the Google Cloud project and location.
BBA does not accept direct provider API keys or direct provider endpoints.
BBA does not accept dedicated Vertex AI endpoint resources.
BBA does not deploy or keep model-serving infrastructure.

Use Application Default Credentials (ADC).
Set the Google Cloud environment before you build the backends:

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="global"
export GOOGLE_GENAI_USE_ENTERPRISE="TRUE"
```

For a hosted BBA controller, use a service account instead of the local `gcloud` login.
Give the service account permission to use the selected models.

`ModelIdentity.publisher` names the model publisher.
`ModelIdentity.model` contains the exact serverless Vertex AI model ID.
BBA selects the ADK connector as follows:

- Gemini model IDs use the native ADK registry.
- Anthropic model IDs use the native ADK registry and the Anthropic Vertex library.
- Other Model Garden MaaS IDs use the ADK `LiteLlm` connector with the `vertex_ai/` route.

The caller does not supply an external-provider adapter:

```python
from bba.adk_runtime import build_adk_backends

creator_backends, solver_backends = build_adk_backends(
    manifest,
    construction_sandbox=secure_sandbox,
)
```

For example, a cohort can contain Gemini, Claude, and two other distinct
serverless configurations that are available in Model Garden.
Check the current Model Garden model cards before you freeze the epoch.
Do not select a model that requires endpoint deployment.
Each selected model must support the function calls that BBA agents use.

`AdkCreatorBackend` creates each live creator agent.
`AdkSolverBackend` creates each live solver agent.
Each backend binds its tools and session to one isolated model run.
BBA does not supply a context-free placeholder agent.

## Epoch protocol

Before an epoch, the controller freezes the data in this list:

- The cohort contains four or more model configurations.
- The cohort contains three or more model families.
- The manifest identifies the GCP project, GCP location, model publishers, model versions, reasoning levels, prompts, tools, and resource limits.
- The epoch has three creator rounds.
- Each solver cell has three separate runs.
- The manifest identifies the public evaluator and its decision limits.
- The manifest contains SHA-256 commitments for the hidden solver panel, hidden seeds, and audit policy.

Round 0 measures blind benchmark creation.
Rounds 1 and 2 give creators only public validation results and public solver failures.
BBA stores each package as an immutable, content-addressed snapshot.
Each later snapshot identifies its parent snapshot.

## Candidate package

Each valid candidate package contains the files in this list:

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

Use the generator command:

```bash
python generator.py --sample-count 30 --seed <seed> --out-dir .
```

Use the verifier command:

```bash
python verifier.py \
  --items solver_bundle/items_private_sample.jsonl \
  --gold gold_private_sample.jsonl
```

Use the scorer command:

```bash
python scorer.py \
  --gold gold_private_sample.jsonl \
  --predictions predictions.jsonl \
  --out score_report.json
```

Use schema version 2 for each score report.
Include the exact `total`, `correct`, and `accuracy` fields.
Put exactly `id` and `answer` in each prediction row and each gold row.

## Validation and isolation

BBA does not trust code from a creator.
`SecureSandbox` never runs candidate code without an audited operating-system boundary.
If the boundary is not available, validation stops with an error.

On Google Cloud, run BBA in a Cloud Run service with the sandbox launcher enabled.
`SecureSandbox` detects `/usr/local/gcp/bin/sandbox` and starts one new ephemeral
sandbox for each command.
The nested sandbox does not receive the controller environment.
Outbound network access is off.
Only the candidate workspace is writable.
The Cloud Run service must have one active epoch worker per instance.
The service must not put credentials, hidden audit data, or other candidate
packages in its container file system.
Store that data outside the worker and transfer only the current workspace.

Cloud Run sandboxes are a Preview Google Cloud feature.
Enable the sandbox launcher when you deploy the service:

```bash
gcloud beta run services update BBA_SERVICE --sandbox-launcher
```

For local development on macOS, `SecureSandbox` uses Seatbelt.
Both backends give each run a temporary home directory and enforce the controller timeout.
If neither backend is available, BBA stops with an error.
The manifest must name the backend that the controller uses.
The default manifest backend is `gcp-cloud-run`.
Use `macos-seatbelt` only for a local development epoch.

The controller makes the checks in this list:

- All entries are regular files or directories.
- The package stays within the tree-size limit.
- The package does not contain links or special files.
- The package declares locked dependencies and a named capability.
- The generator works in a clean directory.
- Two runs with the same seed produce identical payloads.
- A run with a different seed produces a different payload.
- A clean run produces the frozen payload.
- All item IDs and gold IDs agree.
- The public bundle does not contain answer mappings.
- Gold answers score exactly 30/30.
- Independent wrong answers score exactly 0/30.
- The candidate scorer and the controller calculate the same score.

BBA gives each unsuccessful solver result a separate state.
The unsuccessful result states include timeout, provider error, parse error, partial output, and scorer error.
BBA does not use a numeric zero for an unsuccessful result state.
BBA does not rank a creator if the solver panel is incomplete.

## Candidate status

The controller assigns one status to each mechanically valid candidate:

| Status | Rule |
| --- | --- |
| **too easy** | One or more solvers have a median accuracy of 50 percent or more. |
| **solvability audit** | All solvers score zero. |
| **awaiting review** | The candidate is hard and does not have human approval. |
| **frontier challenge** | Human review approves the candidate, but the public solver outcomes do not differ. |
| **active** | Human review approves the candidate. Every solver stays below 50 percent. Solver outcomes differ. |

Canonical promotion requires a signed review from an independent reviewer.
After the package freeze, the controller selects six items.
The reviewer reconstructs the six answers from public material.
The controller accepts an approval only if all six answers are correct.

Each promotion record identifies the data in this list:

- The reviewer
- The candidate digest
- The evidence digests
- The decision
- The known limits
- The time
- The key ID
- The Hash-based Message Authentication Code (HMAC) signature

## Hidden evaluator audit

The controller freezes the audit population and public scores before public closure.
After public closure, the audit authority can release committed audit material.
The digest of each released item must agree with its prior commitment.

The audit uses two targets:

- The **composite holdout target** includes declared shared validation components.
- The **hidden-only target** uses fresh seeds and a hidden solver panel.

The audit compares the public ranks with both targets.

The audit reports the values in this list:

- Spearman agreement
- Global pairwise accuracy
- Shortlist-local pairwise accuracy
- Gap-stratified accuracy
- Matched-defect sensitivity
- Top-quartile regret
- Utility recovery
- Set recovery

The combined BBB-v2 value is a summary only.
The component values remain authoritative.

After BBA releases a holdout, BBA marks the holdout as retired.
BBA does not use a retired holdout again.

The audit uses controlled package defects.
The controlled defects include corrupted keys, duplicate items, truncation, answer leakage, and no-op generators.
The audit also includes a profile that optimizes only for the public solver panel.

## Installation and tests

Use Python 3.10 or later.

Create a virtual environment:

```bash
python3 -m venv .venv
```

Install BBA and its pinned dependencies:

```bash
.venv/bin/pip install -e .
```

Run all tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Examine the production sandbox boundary:

```bash
.venv/bin/python -m bba.cli sandbox-status
```

Validate a candidate package:

```bash
.venv/bin/python -m bba.cli verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260811
```

If an audited sandbox is not available, the validation command stops with an error.

The test suite contains a deterministic 4x4 protocol test.
The test uses three creator rounds and three solver runs for each cell.
It produces 12 immutable snapshots and 144 solver cells.

All fixture backends remain in `tests/`.
The BBA package and the BBA command-line interface do not include the test fixtures.

## Python API

The public Python API has the main interfaces in this table:

| Interface | Function |
| --- | --- |
| `ExperimentManifest` | Stores the frozen epoch protocol and hidden commitments. |
| `TournamentController` | Controls the rounds, validation, matrix, reviews, public closure, and hidden audit. |
| `AdkCreatorBackend` | Runs one creator through Google ADK. |
| `AdkSolverBackend` | Runs one solver through Google ADK. |
| `build_adk_backends` | Makes the ADK backend maps for the exact cohort. |
| `PackageValidator` | Applies the mechanical package checks. |
| `SecureSandbox` | Supplies the fail-closed code boundary. |
| `PromotionRegistry` | Stores the signed, append-only promotion history. |
| `audit_evaluator` | Calculates the BenchBenchBench audit values. |

BBA writes raw evidence to `epochs/<epoch_id>/`.
BBA writes immutable registry records to `registries/`.
Each registry record has a hash link to the prior record.
A correction creates a new record.
BBA does not change a historical record.

## Current status

Version 0.4 sends all model traffic through serverless Vertex AI APIs and supports the Cloud Run sandbox launcher.
It includes the complete BBA protocol, Google ADK 2.6.3 model execution, and the deterministic protocol test.

Each model response must supply token-use data and a frozen model identity.
Each epoch must also supply the required file-system boundary.
BBA never uses an unrestricted host model call or a substitute agent runtime.
