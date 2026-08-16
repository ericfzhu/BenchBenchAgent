# BenchBenchAgent

BenchBenchAgent (BBA) runs two-sided benchmark tournaments in which the same frozen model cohort acts as benchmark creators and blind solvers.

- Creators build executable benchmark designs and deterministic generators.
- BBA freezes every design in a round before selecting that round's evaluation seed.
- Solvers receive only the generated `solver_bundle` and submit locked predictions plus a structured item debrief.
- BBA publishes a creator-by-solver matrix, creator rankings, solver rankings, immutable evidence, and a sealed evaluator audit.
- Independent solvability evidence and a separate signed human decision are required before a final-round benchmark becomes canonical.

BBA is a local-first application. Google Cloud is used only for serverless model inference through Google ADK and Vertex AI. Controller state, immutable evidence, validation, scoring, generated-code sandboxing, review records, ranking, and audit calculations stay on the operator's machine.

Version `0.13.0` includes a localhost development portal, redacted ADK observability, retry-safe call/token/cost accounting, review-freeze enforcement, crash-safe public-close recovery, and a sealed-audit sandbox bound to the frozen epoch backend.

Paid Vertex and independent production-acceptance evidence are still required. See [implementation status](docs/implementation-status.md) and [production acceptance](docs/production-acceptance.md).

## Requirements

- Python 3.10 or later
- Google Cloud CLI
- A billed Google Cloud project with Vertex AI enabled
- Application Default Credentials (ADC)
- Access, accepted terms, and quota for every model in the frozen catalog
- Bubblewrap on Ubuntu Linux or Seatbelt on macOS

The current generated-code wheel catalog is empty, so creator packages must use the Python standard library and an empty or comment-only `requirements.lock`.

## Install on Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  bubblewrap \
  apparmor \
  apparmor-utils \
  apparmor-profiles

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e .
```

Ubuntu must permit Bubblewrap to create the required namespaces. See [Ubuntu and Google Cloud readiness](docs/ubuntu-gcp-readiness.md) for AppArmor troubleshooting.

Run the local suite and sandbox check:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/bba sandbox-status
```

A skipped security test is not target-host sandbox proof.

## Google Cloud setup

`gcloud auth login` authenticates the Cloud SDK. BBA's Python libraries use Application Default Credentials instead.

```bash
gcloud config set project PROJECT_ID
gcloud services enable aiplatform.googleapis.com --project=PROJECT_ID
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
export GOOGLE_CLOUD_PROJECT=PROJECT_ID
```

BBA freezes the project into the epoch and propagates it to both native Google/Anthropic adapters and LiteLLM Vertex routes. The current catalog uses the Vertex `global` location.

Show the frozen source catalog:

```bash
.venv/bin/bba catalog
```

## Local development portal

The recommended local interface is the development portal:

```bash
.venv/bin/bba web --evidence-root .bba
```

Open `http://127.0.0.1:8765`.

The workspace shows:

- sandbox, ADC/project, price-catalog, and dependency-policy readiness;
- serialized buttons for the sandbox check, catalog check, and complete local unit suite;
- saved epochs and recent operations;
- phase-aware epoch controls;
- failed work and saved error messages;
- model-call usage and conservative USD usage against the frozen ceiling;
- candidate review, rankings, and redacted ADK activity.

The portal binds only to IPv4 loopback, enforces host/origin/form-token checks, denies framing, and runs one change operation at a time. Do not expose it through a tunnel, proxy, container port, or non-loopback interface.

See [Local development portal](docs/development-portal.md).

## CLI workflow

Create an epoch:

```bash
.venv/bin/bba epoch create --evidence-root .bba
```

The manifest freezes the model catalog, project, global location, evaluator identity, sandbox backend, budgets, retry policy, and sealed commitments.

Run the paid preflight before public work:

```bash
.venv/bin/bba epoch preflight \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Preflight checks every catalog route and requires a complete passing record for the exact manifest. It also checks the conservative retry-inclusive USD estimate against the frozen hard ceiling. Failed attempts remain diagnostic evidence; only a complete pass becomes `preflight/vertex.json`.

Run or resume the public tournament:

```bash
.venv/bin/bba epoch run \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The current 12-model, three-round cohort plans 36 creator invocations and, when all designs validate, 1,296 public solver cells. The sealed audit can later add 432 hidden solver cells on eligible final-round snapshots.

BBA reserves calls, input tokens, output tokens, and a conservative USD amount before each model attempt. Creator retries use distinct reservations. Timeout and provider failures are the only retryable solver states; immutable attempts are never overwritten.

Inspect progress:

```bash
.venv/bin/bba epoch status --epoch-id EPOCH_ID --evidence-root .bba
.venv/bin/bba epoch candidates --epoch-id EPOCH_ID --evidence-root .bba
.venv/bin/bba epoch observability --epoch-id EPOCH_ID --evidence-root .bba
```

When public work reaches `awaiting_review`, final-round candidates can receive independent solvability certificates and signed decisions. The review window closes permanently when the public audit population is frozen. Late certificate or review requests are rejected before any review-adjacent registry mutation occurs.

Freeze public audit inputs, then close public results:

```bash
.venv/bin/bba epoch freeze-audit --epoch-id EPOCH_ID --evidence-root .bba
.venv/bin/bba epoch close --epoch-id EPOCH_ID --evidence-root .bba
```

The same available sandbox backend frozen in the manifest is required for audit-population generation and the sealed audit. Public closure is restart-safe: rerunning it repairs any approved canonical-promotion publication that was interrupted after the public evaluation record was written.

After public closure, run the sealed audit:

```bash
.venv/bin/bba epoch audit --epoch-id EPOCH_ID --evidence-root .bba
```

Hidden material opens only after public closure, hidden solver results do not alter the frozen public rankings, and revealed holdout material is retired after audit.

## Candidate package check

Validate an executable candidate locally:

```bash
.venv/bin/bba verify-package \
  --package /absolute/path/to/candidate \
  --seed 20260812
```

Generated candidate code runs in the credential-free operating-system sandbox. BBA independently recomputes exact-match scoring and compares it with the candidate scorer.

## Saved state and evidence

BBA stores workflow state in `.bba/bba-state.sqlite3` and immutable epoch evidence under `.bba/epochs/EPOCH_ID/` by default.

If a process stops, rerun the same command. Completed immutable evidence is restored; interrupted work is reset under the frozen retry and budget rules. A local file lock prevents two processes from mutating the same epoch concurrently.

Successful solver attempts preserve predictions, structured debriefs, candidate and controller scorer reports, command diagnostics, digests, and per-item results. `bba evidence replay-cell` can replay a successful public or hidden score without inference.

## Observability

BBA records content-free ADK lifecycle metadata: model identity, call count, tool names, token counts, latency, model version, status, and error type. It does not record prompts, tool arguments, tool results, model output, predictions, debrief text, private gold, or hidden audit content.

Optional OpenTelemetry export is loopback-only and off by default:

```bash
export BBA_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318
```

Trace export is operational telemetry, not immutable epoch evidence or recovery state.

## Documents

- [Protocol specification](docs/protocol.md) — normative rules for an epoch.
- [Operations guide](docs/operations.md) — complete local workflow and command formats.
- [Local development portal](docs/development-portal.md) — browser workspace and diagnostics.
- [Ubuntu and Google Cloud readiness](docs/ubuntu-gcp-readiness.md) — ADC, Bubblewrap, AppArmor, and paid preflight checks.
- [Implementation status](docs/implementation-status.md) — implemented behavior and remaining external proof.
- [Completion plan](docs/implementation-plan.md) — historical implementation work plan and exit gates.
- [Production acceptance](docs/production-acceptance.md) — evidence required before production verification.

## Main Python interfaces

| Interface | Function |
| --- | --- |
| `ExperimentManifest` | Stores the frozen epoch configuration. |
| `TournamentController` | Controls restore, public work, review, closure, and audit. |
| `LocalStateStore` | Stores transactional workflow and inference-budget state. |
| `EvidenceStore` | Stores immutable local evidence and review-freeze markers. |
| `AdkCreatorBackend` | Runs one creator with ADK and Vertex AI. |
| `AdkSolverBackend` | Runs one solver with ADK and Vertex AI. |
| `PackageValidator` | Validates generated benchmarks and scorer behavior. |
| `SecureSandbox` | Runs generated code inside the frozen local OS boundary. |
| `PromotionRegistry` | Stores and verifies signed promotion records. |
| `SealedAuditRunner` | Runs the committed hidden experiment and damage tests. |
