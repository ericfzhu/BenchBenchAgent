# BenchBenchAgent

BenchBenchAgent (BBA) tests models that make and solve executable benchmarks.
Each model has two separate roles.

- A creator makes a benchmark design and deterministic generator.
- BBA freezes the design and then selects an evaluation seed.
- A solver solves the generated evaluation instance.

BBA makes a creator-by-solver score matrix.
BBA also makes separate creator and solver ranks.
Independent evidence must certify solvability. A separate human reviewer must approve the certificate and benchmark before BBA adds it to the canonical registry.
A sealed audit tests the public evaluator after the public epoch is closed.
Version `0.12.0` also includes a localhost operator console.
The console controls the same local controller and evidence as the CLI.
Paid Vertex and full production acceptance evidence are still required.
See the [implementation status](docs/implementation-status.md) before you run a production epoch.

BBA uses the method from Rohit Krishnan's [BenchBench](https://www.strangeloopcanon.com/p/introducing-benchbench).
BBA uses the sealed evaluator audit from Ethan Mollick's [BenchBenchBench](https://github.com/emollick/benchbenchbench).

## Operating model

BBA is a local application.
It keeps these functions on the operator's machine:

- Epoch control and recovery
- SQLite workflow state
- Immutable evidence files
- Package validation and scoring
- Generated-code sandbox execution
- Human review records
- Public ranks and holdout audit calculations

BBA uses Google Cloud only for serverless model inference.
It does not use Cloud Run, Cloud Storage, Firestore, Cloud Tasks, or a deployed model endpoint.
Google Python Agent Development Kit (ADK) 2.6.3 controls each model session.

BBA owns the model cohort and all model routes.
The operator does not supply a manifest, model ID, provider, or location.
BBA uses a versioned source catalog and the Google Cloud `global` location.
An epoch stores a complete copy of this catalog in its immutable manifest.

BBA supports local execution on Ubuntu Linux and macOS.
Ubuntu uses Bubblewrap namespaces.
macOS uses Seatbelt.
The local sandbox must stop network access and host file access for generated code.
BBA stops if the sandbox is not available.

## Saved state and resume

BBA saves progress after each creator run, validation, and solver cell.
It stores workflow state in `.bba/bba-state.sqlite3` by default.
It stores immutable evidence in `.bba/epochs/EPOCH_ID/`.

If the process stops, run the same `epoch run` command again.
BBA finds complete evidence and does not repeat that work.
BBA resets an interrupted work item and starts that item again.
A local file lock prevents two processes from changing one epoch at the same time.

## Install

Use Python 3.10 or later.

On Ubuntu, install Bubblewrap first:

```bash
sudo apt-get update
sudo apt-get install bubblewrap
```

The Ubuntu kernel must permit Bubblewrap to make an unprivileged user namespace.

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
.venv/bin/bba sandbox-status
```

## Google Cloud setup

Enable Vertex AI and create local Application Default Credentials.

```bash
gcloud config set project PROJECT_ID
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login
```

Accept the Model Garden terms for the models in the BBA catalog.
BBA sets the ADK location and Google Cloud mode.
If ADC cannot find the project, set `GOOGLE_CLOUD_PROJECT`.

Show the catalog:

```bash
.venv/bin/bba catalog
```

## Run an epoch

You can use the CLI or the localhost console.
Both interfaces use the same saved state and immutable evidence.

Start the console:

```bash
.venv/bin/bba web \
  --evidence-root .bba
```

Open `http://127.0.0.1:8765` in a browser.
The console can create an epoch, run the paid preflight, run or resume public work, and show saved progress.
It can also record solvability evidence and a signed candidate decision.
After public closure, it shows the creator ranking, solver ranking, and creator-by-solver matrix.
After the sealed audit, it also shows the evaluator audit status.

The console binds only to `127.0.0.1`.
It rejects requests from other hosts and origins.
It runs one change operation at a time.
Paid and irreversible operations require a confirmation in the page.
Do not use a port proxy or expose this console to a network.

See the [operations guide](docs/operations.md#10-use-the-localhost-console) for the complete console workflow.

## Run an epoch with the CLI

Create the local epoch:

```bash
.venv/bin/bba epoch create \
  --evidence-root .bba
```

BBA gets the project from ADC.
BBA creates the epoch ID, hidden seeds, audit commitments, and immutable manifest.
The command prints the new epoch ID.

You can supply a readable local ID if required:

```bash
.venv/bin/bba epoch create \
  --epoch-id my-first-epoch \
  --evidence-root .bba
```

Run or resume the public tournament:

```bash
.venv/bin/bba epoch run \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Inspect saved progress at any time:

```bash
.venv/bin/bba epoch status \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The public run has three creator rounds.
Each cohort model creates one benchmark snapshot in each round.
Each valid snapshot receives three runs from every public solver.
With the current 12-model cohort, the planned public run has 36 creator
invocations and 1,296 solver cells if all designs pass validation.
The sealed audit later adds 432 hidden solver cells on final-round snapshots.
It validates the evaluator and does not change the frozen public ranks.
Every successful solver cell locks its predictions and then submits a structured debrief.
The next creator round receives a bounded public feedback report with correctness labels.
In each round, BBA freezes all creator designs before it selects the round seed.
BBA does not give the seed to a creator.
BBA uses the seed to generate and freeze one evaluation instance from each design.
Each valid snapshot receives the full blind solver panel.
The command ends when all required public evidence exists.

Human review, public closure, and holdout audit use separate commands.
This separation keeps hidden evidence outside the creator feedback loop.
See the [operations guide](docs/operations.md) for the complete command sequence and input file formats.

## Candidate package check

Use this command to validate one package:

```bash
.venv/bin/bba verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260812
```

The command returns a nonzero status if the package is not valid.

## Main outputs

BBA stores these outputs:

- Immutable benchmark-design snapshots and revision links
- Controller-selected round seeds
- Immutable generated evaluation instances
- Validation records
- Tagged solver-cell records
- Immutable solver-attempt artifacts and replay reports
- Creator and solver ranks
- Signed promotion records
- Typed, digest-bound solvability certificates
- Public evaluation records
- Holdout audit records
- An append-only benchmark registry

Non-success solver states do not contain a numeric score.
A timeout or provider error is not a zero score.

## Documents

- [Protocol specification](docs/protocol.md): Required rules for one epoch.
- [Operations guide](docs/operations.md): Local setup, commands, recovery, review, and audit.
- [Implementation status](docs/implementation-status.md): Known incomplete work and completion conditions.
- [Completion plan](docs/implementation-plan.md): Work order, protocol decisions, tests, and exit gates.
- [Production acceptance](docs/production-acceptance.md): Required paid and independent evidence for release verification.

## Main Python interfaces

| Interface | Function |
| --- | --- |
| `ExperimentManifest` | Stores the frozen epoch configuration. |
| `TournamentController` | Controls restore, public work, review, closure, and audit. |
| `LocalStateStore` | Stores transactional local workflow state. |
| `EvidenceStore` | Stores immutable local evidence. |
| `AdkCreatorBackend` | Runs one creator with ADK and Vertex AI. |
| `AdkSolverBackend` | Runs one solver with ADK and Vertex AI. |
| `PackageValidator` | Checks a benchmark design and its generated instance. |
| `SecureSandbox` | Runs generated code in a local operating-system sandbox. |
| `PromotionRegistry` | Stores signed promotion records. |
| `SealedAuditRunner` | Runs the committed hidden experiment and damage tests. |
