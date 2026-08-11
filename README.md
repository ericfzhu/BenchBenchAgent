# BenchBenchAgent

BenchBenchAgent (BBA) evaluates models that make and solve executable benchmarks.
Each model has two separate roles.

- A creator makes a benchmark package.
- A solver solves each valid benchmark package.

BBA makes a creator-by-solver score matrix.
BBA also makes separate creator and solver ranks.
An independent reviewer must approve a benchmark before BBA adds it to the canonical registry.
A sealed audit tests the public evaluator after the public epoch is closed.

BBA uses the method from Rohit Krishnan's [BenchBench](https://www.strangeloopcanon.com/p/introducing-benchbench).
BBA uses the sealed evaluator audit from Ethan Mollick's [BenchBenchBench](https://github.com/emollick/benchbenchbench).

## System limits

BBA has these fixed limits:

- BBA uses Google Python Agent Development Kit (ADK) 2.6.3.
- BBA sends model requests through serverless Vertex AI APIs.
- BBA does not use direct provider API keys.
- BBA does not accept deployed model endpoints.
- BBA runs creator code only in an approved operating-system sandbox.
- BBA stops if the sandbox is not available.

The controller has no production `run-epoch` command at this time.
You can use the Python API to run one epoch in one process.
The controller cannot restore a live epoch after the process stops.
Do not use the present API for an unattended production epoch.

## Main outputs

BBA stores these outputs:

- Immutable candidate snapshots
- Validation records
- Solver-cell records
- Creator and solver ranks
- Signed promotion records
- Public evaluation records
- Holdout audit records
- An append-only benchmark registry

BBA writes epoch evidence to `epochs/<epoch_id>/`.
BBA writes registry records to `registries/`.

## Quick start

Use Python 3.10 or later.

Create an environment and install BBA:

```bash
python3.10 -m venv .venv
.venv/bin/pip install -e .
```

Run the tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Check the local sandbox:

```bash
.venv/bin/python -m bba.cli sandbox-status
```

Authenticate to Google Cloud:

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="global"
export GOOGLE_GENAI_USE_ENTERPRISE="TRUE"
```

The example manifest is in [`examples/serverless-pilot-manifest.json`](examples/serverless-pilot-manifest.json).
Replace the project ID and all hidden commitments before you use the file.

Read the [operations guide](docs/operations.md) before you send a live model request.
The guide gives the GCP setup, model checks, sandbox checks, epoch sequence, review sequence, and audit sequence.

## Candidate package check

Use this command to validate one candidate package:

```bash
.venv/bin/python -m bba.cli verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260812
```

The command returns a nonzero status if the package is not valid.

## Documents

- [Protocol specification](docs/protocol.md): Normative rules for an epoch.
- [Operations guide](docs/operations.md): Setup and operating procedures.
- [Serverless pilot manifest](examples/serverless-pilot-manifest.json): A small cohort template.

## Public Python interfaces

| Interface | Function |
| --- | --- |
| `ExperimentManifest` | Stores the frozen epoch configuration. |
| `TournamentController` | Controls the public epoch, review, closure, and audit. |
| `AdkCreatorBackend` | Runs one creator with ADK. |
| `AdkSolverBackend` | Runs one solver with ADK. |
| `build_adk_backends` | Makes the creator and solver backends. |
| `PackageValidator` | Checks a candidate package. |
| `SecureSandbox` | Runs creator code in an approved sandbox. |
| `PromotionRegistry` | Stores signed promotion records. |
| `audit_evaluator` | Calculates the holdout audit values. |

## Current implementation

Version 0.4 contains the full protocol data model and the deterministic end-to-end test.
The test runs four models through three rounds.
The test makes 12 snapshots and 144 solver cells.

The command-line interface supports package validation and sandbox inspection.
The next operating milestone is a restart-safe epoch command.
