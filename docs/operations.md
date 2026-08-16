# BBA operations guide

This guide describes the current local workflow for BBA version `0.13.0` and protocol `bba.epoch.v8`. Read the [protocol specification](protocol.md) before running paid work.

## 1. System boundary

BBA runs the controller locally. The operator machine owns:

- SQLite workflow and inference-budget state;
- immutable evidence;
- creator workspaces;
- generated-code sandboxing;
- validation and scoring;
- review records and signature verification;
- public ranking and sealed-audit calculations;
- the localhost development portal.

Google Cloud is used only for serverless model inference. BBA does not require Cloud Run, Cloud Storage, Firestore, Cloud Tasks, or deployed model endpoints.

Protect and back up the complete evidence root. Do not edit evidence files or the SQLite database by hand.

## 2. Required items

Prepare:

- Python 3.10 or later;
- Google Cloud CLI;
- a billed Google Cloud project;
- Vertex AI API access;
- Application Default Credentials;
- accepted terms and quota for every model in the frozen BBA catalog;
- Bubblewrap on Ubuntu or Seatbelt on macOS;
- sufficient local disk space;
- an independent certificate issuer and a separate human adjudicator for any benchmark you want to approve canonically.

The current candidate wheel catalog is empty. Creator packages must therefore use only the Python standard library and an empty or comment-only `requirements.lock`.

## 3. Install BBA

Ubuntu:

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

Confirm the pinned ADK release:

```bash
.venv/bin/python -c "import google.adk; print(google.adk.__version__)"
```

It must print `2.6.3`.

Run the complete local suite:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

A skipped security test is not sandbox proof. On Ubuntu, the target-host run must exercise the Bubblewrap security tests rather than skip them.

## 4. Prepare Google Cloud

Select the project and enable Vertex AI:

```bash
gcloud config set project PROJECT_ID
gcloud services enable aiplatform.googleapis.com --project=PROJECT_ID
```

Use a role that permits the required Vertex AI operations. Accept required Model Garden or partner-model terms and obtain sufficient quota for every frozen route before the paid preflight.

The current catalog is BBA-owned. The operator cannot replace a frozen model route with a deployed endpoint or another provider.

Inspect the catalog:

```bash
.venv/bin/bba catalog
```

## 5. Authenticate the Python process

`gcloud auth login` alone is not enough. Create Application Default Credentials:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
export GOOGLE_CLOUD_PROJECT=PROJECT_ID
```

BBA freezes the resolved project into the epoch. It propagates the project and `global` location to both native ADK adapters and LiteLLM Vertex routes.

Never place credential files in the evidence root, a candidate directory, or a generated-code workspace.

## 6. Check local readiness

Check the generated-code boundary:

```bash
.venv/bin/bba sandbox-status
```

On Ubuntu, `backend` and `expected_backend` must both be `linux-bubblewrap` and `available` must be `true`. On macOS, the equivalent backend is `macos-seatbelt`.

The frozen backend is an epoch invariant. Public validation, audit-population generation, and sealed audit all require an available sandbox whose backend matches the manifest.

For Ubuntu AppArmor troubleshooting and a full smoke checklist, see [Ubuntu and Google Cloud readiness](ubuntu-gcp-readiness.md).

## 7. Use the local development portal

The recommended development/operator interface is:

```bash
.venv/bin/bba web --evidence-root .bba
```

Open:

```text
http://127.0.0.1:8765
```

The workspace page shows:

- sandbox readiness;
- ADC and selected project readiness;
- price-catalog coverage;
- dependency policy;
- saved epochs and their phase/progress;
- recent operations.

It can serialize these local diagnostics through the same one-operation queue used by epoch mutations:

- sandbox status;
- catalog inspection;
- the complete local unit-test suite.

Each epoch page presents the workflow in order:

1. setup;
2. paid preflight;
3. public tournament;
4. human review;
5. freeze audit inputs;
6. publish public results;
7. sealed audit.

Controls that are invalid for the current phase are disabled. An active job disables other changes. Failed controller work is displayed with its work ID and saved error. The page also reports saved model-call usage and the conservative USD estimate against the frozen ceiling.

Final-round candidate pages contain separate certificate and signed-decision forms. These forms become read-only when the audit population is frozen.

The portal is localhost-only. It uses trusted-host, origin, form-token, frame-denial, and restrictive content-security checks. Do not expose it through a tunnel, proxy, container port, or non-loopback interface.

See [Local development portal](development-portal.md).

## 8. Create an epoch

Use one evidence root for the complete lifecycle:

```bash
.venv/bin/bba epoch create --evidence-root .bba
```

Or provide a local filesystem-safe name:

```bash
.venv/bin/bba epoch create \
  --epoch-id my-first-epoch \
  --evidence-root .bba
```

Epoch creation freezes:

- the project and `global` location;
- the complete source model cohort;
- behavior settings;
- the evaluator identity and installed routing/runtime versions;
- resource and retry limits;
- the conservative USD ceiling;
- the local sandbox backend;
- hidden commitments and sealed private material.

Do not edit the manifest or private holdout material after creation.

## 9. Run the paid preflight

Before public work:

```bash
.venv/bin/bba epoch preflight \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Preflight checks every frozen model route for access, `global` routing, tool use, token-use metadata, and returned provider identity metadata when available. It does not deploy an endpoint.

Preflight also verifies the local sandbox and the conservative retry-inclusive cost estimate. A complete price catalog is required; the estimate must not exceed `manifest.budget.max_estimated_cost_usd`.

Failures are kept under `preflight-attempts/` for diagnosis. Only a complete passing run is frozen as:

```text
epochs/EPOCH_ID/preflight/vertex.json
```

`epoch run` requires that passing record to match the exact frozen manifest.

For a traceback:

```bash
BBA_DEBUG=1 .venv/bin/bba epoch preflight \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

## 10. Run or resume the public tournament

```bash
.venv/bin/bba epoch run \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The controller:

1. acquires the epoch lock;
2. restores immutable evidence and SQLite state;
3. resets interrupted work;
4. runs unfinished creator work;
5. freezes all designs in the current round;
6. selects the round seed only after all designs are frozen;
7. materializes and validates evaluation instances;
8. runs unfinished public solver cells;
9. freezes attempt/cell evidence before marking work complete;
10. advances to the next round.

The current 12-model catalog plans 36 creator invocations. If every candidate validates, the public tournament contains 1,296 solver cells.

### Retry and budget behavior

Each model attempt reserves:

- model calls;
- input tokens;
- output tokens;
- a conservative USD amount.

Reservations are transactional and frozen. Creator retries use a distinct reservation for each work-item attempt, so a failed or interrupted creator cannot reuse the prior attempt's allowance. Solver attempts already have unique attempt IDs.

Only solver `timeout` and `provider_error` states are retryable, up to the frozen maximum attempt count. Parse, partial-prediction, scorer, invalid-bundle, and success states are not retried.

The controller uses a frozen two-times cost safety factor for runtime reservations. It stops before a new reservation can exceed the epoch's call, token, or USD ceiling. Google Cloud budgets and alerts are still recommended as an independent external control.

### Resume

You may stop and rerun the same command. Completed immutable work is restored rather than repeated. An interrupted work item is reset and starts its next local attempt under the frozen budget rules.

Do not run two mutation commands for the same epoch concurrently. The epoch lock rejects the second process.

## 11. Inspect progress

```bash
.venv/bin/bba epoch status --epoch-id EPOCH_ID --evidence-root .bba
.venv/bin/bba epoch candidates --epoch-id EPOCH_ID --evidence-root .bba
.venv/bin/bba epoch observability --epoch-id EPOCH_ID --evidence-root .bba
```

The public tournament reaches `awaiting_review` when all required creator, validation, and public solver work is complete.

A failed controller work item appears in `failed_work`. Correct the local/provider problem and rerun the appropriate command.

## 12. Certify solvability

Only final-round snapshots can receive a canonical solvability certificate.

Supported certificate types:

- `human_reconstruction`;
- `independent_reference`;
- `machine_verifiable_witness`;
- `trusted_external_source`;
- `independent_solver`.

The certificate issuer cannot be the benchmark creator.

For human reconstruction, obtain the six controller-selected item IDs:

```bash
.venv/bin/bba epoch certificate-items \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --evidence-root .bba
```

Save all six reconstructed answers in one JSON object and record the certificate:

```bash
.venv/bin/bba epoch record-certificate \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --type human_reconstruction \
  --issuer-id CERTIFICATE_ISSUER_ID \
  --independence-basis "The issuer did not create this benchmark." \
  --verification-method "Reconstructed all selected answers from public material." \
  --scope "Six controller-selected items." \
  --answers certificate-answers.json \
  --evidence working-notes.md=/protected/path/working-notes.md \
  --evidence-root .bba
```

For non-human certificate types, omit `--answers` and supply one or more independent evidence files.

## 13. Record signed human adjudication

Save the seven required findings as JSON:

```json
{
  "named_capability_valid": true,
  "public_materials_sufficient": true,
  "oracle_consistent": true,
  "scorer_consistent": true,
  "no_arbitrary_obscurity": true,
  "useful_evaluation": true,
  "solvability_certificate_adequate": true
}
```

Record the decision:

```bash
.venv/bin/bba epoch record-review \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --reviewer-id REVIEWER_ID \
  --solvability-certificate-digest CERTIFICATE_DIGEST \
  --findings reviewer-findings.json \
  --decision approved \
  --limitation "LIMITATION TEXT" \
  --key-id REVIEWER_KEY_ID \
  --signing-key-file /protected/path/reviewer.key \
  --public-key-file reviewer-public-key.pem \
  --evidence-root .bba
```

The approving reviewer must differ from the certificate issuer. Approval requires passed mechanical validation, a complete successful solver panel, an eligible final-round status, and all findings to pass.

Keep the private signing key outside the evidence root.

### Review freeze boundary

The review window closes permanently when `epoch freeze-audit` successfully freezes the public audit population. After that point:

- no new certificate can be recorded;
- no new human decision can be recorded;
- direct CLI/portal retries are rejected by the evidence/controller boundary before review-adjacent registry mutation.

Complete all intended review work before freezing audit inputs.

## 14. Freeze the public audit population

```bash
.venv/bin/bba epoch freeze-audit \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

This builds public evaluator profiles, matched damage profiles, and the public-optimizer control entirely from stored public evidence.

The command requires an available sandbox whose backend matches the epoch manifest. This prevents a public epoch created under one isolation boundary from preparing audit inputs under another.

Successful freeze closes the human review window.

## 15. Close the public epoch

```bash
.venv/bin/bba epoch close \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The public record contains the matrix, candidate statuses, blind and final creator ranks, adaptation values, and solver rankings. Hidden evidence is not included.

Approved promotions are appended to the canonical registry only after public closure. Closure is crash-safe: if the process stops after the public evaluation record is written but before canonical publication finishes, rerunning `epoch close` idempotently repairs the missing approved registry entries.

## 16. Run the sealed audit

After public closure:

```bash
.venv/bin/bba epoch audit \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The audit requires the same available sandbox backend frozen in the manifest.

BBA then:

- opens and verifies the committed private material;
- creates fresh hidden instances from committed seeds;
- runs the committed hidden solver panel;
- runs the controlled damage tests;
- derives both target vectors from stored evidence;
- publishes the complete audit vector and status;
- retires the revealed holdout.

Hidden debriefs never enter public creator feedback. Hidden results do not change already-frozen public rankings.

## 17. Offline replay

Replay any successful public or hidden solver attempt without a new model call:

```bash
.venv/bin/bba evidence replay-cell \
  --epoch-id EPOCH_ID \
  --attempt-id ATTEMPT_ID \
  --evidence-root .bba
```

Replay verifies prediction, debrief, instance, and controller-score evidence before reporting success.

## 18. Local evidence layout

A typical evidence root contains:

```text
bba-state.sqlite3
locks/
epochs/
  EPOCH_ID/
    manifest.json
    private/
      holdout-plan.json
    preflight/
    preflight-attempts/
    candidates/
    round-seeds/
    instances/
    validations/
    solver-attempts/
    solver-cells/
    agent-traces/
    observability/
    solvability-certificates/
    promotions/
    evaluation/
    audit/
    state/
registries/
  reviewer-trust/
  promotion-history/
  canonical-benchmarks/
```

Do not modify these files manually. Exclude `private/` from any public evidence publication.

## 19. Observability and tracing

BBA stores redacted ADK lifecycle records locally. They can contain model identity, call counts, tool names, token use, latency, provider model version, status, and error type. They must not contain prompts, tool arguments/results, model output, predictions, debrief text, private gold, or hidden audit content.

Optional OpenTelemetry export is loopback-only:

```bash
export BBA_OTLP_TRACES_ENDPOINT=http://127.0.0.1:4318
```

Export failure does not stop an epoch and exported traces are not evidence or recovery state.

## 20. Failure response

After a failure:

- keep all published evidence;
- inspect `epoch status` and portal failed-work output;
- correct the local/provider problem;
- rerun the same operation;
- never convert a non-success solver state to numeric zero;
- never modify or delete prior immutable attempts;
- never add human evidence after audit freeze;
- never reveal holdout material before public closure;
- never reuse retired holdout material.

For development and target-host release gates, use [Implementation status](implementation-status.md) and [Production acceptance](production-acceptance.md).
