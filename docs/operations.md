# BBA operations guide

This guide tells an operator how to run one BBA epoch.
Read the [protocol specification](protocol.md) before you run a paid epoch.

## 1. System boundary

BBA runs on one local machine.
BBA uses local resources for these functions:

- The controller
- SQLite workflow state
- Evidence storage
- Package generation workspaces
- Sandbox execution
- Validation and scoring
- Review records
- Rank and audit calculations

BBA uses Google Cloud only for serverless model inference.
BBA does not deploy a model.
BBA does not use a Google Cloud database, queue, storage bucket, or compute service.

The operator must back up the local evidence root.
The backup must keep file contents and directory names unchanged.

## 2. Required items

Prepare these items:

- Python 3.10 or later
- Google Cloud CLI
- A Google Cloud project with billing
- Vertex AI API access
- Access to each model in the BBA serverless catalog
- A supported local operating-system sandbox
- Sufficient local disk space
- An independent reviewer

Google documents the current Vertex AI setup in the [Vertex AI quickstart](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart).

## 3. Install BBA

On Ubuntu, install Bubblewrap:

```bash
sudo apt-get update
sudo apt-get install bubblewrap
```

The Ubuntu kernel must permit Bubblewrap to make an unprivileged user namespace.
BBA checks this function before it starts an epoch.

Create a virtual environment and install BBA:

```bash
python3.10 -m venv .venv
.venv/bin/pip install -e .
```

Confirm the ADK version:

```bash
.venv/bin/python -c "import google.adk; print(google.adk.__version__)"
```

The command must print `2.6.3`.

Run all tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 4. Prepare Google Cloud

Select the project and enable Vertex AI:

```bash
gcloud config set project PROJECT_ID
gcloud services enable aiplatform.googleapis.com
```

Give the local operator `roles/aiplatform.user` or a narrower custom role.
Open each catalog model card in Model Garden.
Accept the model terms when Google Cloud requests this action.

BBA owns the catalog.
The operator cannot add a self-deployed model to an epoch.
Model availability, quota, price, and terms can change.
Check the catalog before each epoch:

```bash
.venv/bin/bba catalog
```

## 5. Authenticate the local process

Create local Application Default Credentials:

```bash
gcloud auth application-default login
```

Set the ADC quota project:

```bash
gcloud auth application-default set-quota-project PROJECT_ID
```

BBA gets the project from ADC.
BBA sets the `global` location and the Google Cloud ADK mode.
If ADC cannot find the project, set `GOOGLE_CLOUD_PROJECT`.

Do not put a credential file in the evidence root or a candidate workspace.
Generated code cannot read the local credentials.

## 6. Check the local sandbox

Run this command:

```bash
.venv/bin/bba sandbox-status
```

On Ubuntu, the result must show `linux-bubblewrap` and `available: true`.
On macOS, the result must show `macos-seatbelt` and `available: true`.
BBA stops before inference if the required sandbox is not available.
Create and run one epoch on the same operating-system backend.
The immutable manifest records this backend.

The sandbox has no network access.
The sandbox cannot read controller credentials, other candidates, or holdout files.

## 7. BBA-owned epoch configuration

BBA contains one versioned serverless model catalog.
The catalog contains the exact Google Cloud model ID, ADK route, model family, reasoning mode, and tool mode.
The operator does not edit these values.

Catalog `gcp-serverless-2026-08-12` contains these models:

- `gemini-3.6-flash`
- `gemini-3.5-flash`
- `gemini-3.5-flash-lite`
- `gemini-3.1-pro-preview`
- `claude-sonnet-5`
- `claude-opus-5`
- `claude-fable-5`
- `claude-opus-4-8`
- `claude-opus-4-7`
- `claude-sonnet-4-6`
- `claude-opus-4-6`
- `grok-4.3`

Gemini 3.1 Pro and Grok 4.3 are Preview models in this catalog version.
Grok 4.3 uses fixed quota.

BBA also owns these protocol values:

- Resource limits
- Decision limits
- Prompt digests
- Evaluator version
- Sandbox requirements
- Hidden commitment format

An epoch manifest contains a frozen copy of these values.
The manifest is evidence, not operator input.

## 8. Automatic hidden commitments

BBA creates the hidden solver configuration, hidden seeds, and audit policy before the first creator run.
BBA stores this material in `private/holdout-plan.json` under the local epoch directory.
BBA puts only the SHA-256 commitments in `manifest.json`.

The creator and solver processes cannot read the private directory.
Do not publish the private directory with public epoch evidence.
Do not reveal its contents before public closure.

## 9. Create the local epoch

Use one evidence root for all local BBA data.
The default root is `.bba`.

```bash
.venv/bin/bba epoch create \
  --evidence-root .bba
```

This command gets the project from ADC.
It creates the epoch ID, hidden material, commitments, and manifest.
It also creates the local SQLite state file.
The command prints the epoch ID.

Use `--epoch-id NAME` only when you need a specific local name.
You cannot change the manifest or private material after this command.

## 10. Run or resume the public tournament

Run the small paid Vertex preflight first:

```bash
.venv/bin/bba epoch preflight \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The command checks every frozen model route and tool contract.
It prints the frozen invocation and token limits.
It prints a dollar estimate only when the local catalog has an exact published price for every route.
It does not deploy an endpoint.

Then run this command:

```bash
.venv/bin/bba epoch run \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The command does this work:

1. It locks the local epoch.
2. It restores complete evidence.
3. It resets a work item that a prior process interrupted.
4. It runs unfinished creator work through Vertex AI.
5. It freezes all benchmark designs in the current round.
6. It selects and freezes one round evaluation seed.
7. It generates and freezes one evaluation instance from each design.
8. It validates each new instance in the local sandbox.
9. It runs unfinished solver cells through Vertex AI.
10. It saves each result before it starts the next work item.

The creator does not receive the round seed.
The round seed does not make the creator model deterministic.
It makes each frozen generator reproduce the same evaluation instance.

You can stop the process between work items.
If the process stops during one item, BBA starts that item again.
Run the same command to resume.

Do not start two `epoch run` processes for one epoch.
The local lock rejects the second process.

## 11. Inspect progress

Show the phase and work counts:

```bash
.venv/bin/bba epoch status \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Show all snapshots:

```bash
.venv/bin/bba epoch candidates \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The public run is complete when the phase is `awaiting_review`.
A failed work item appears in `failed_work`.
Correct the local or provider fault and run `epoch run` again.

## 12. Record human reviews

Review only final-round snapshots.
Get the controller-selected item IDs:

```bash
.venv/bin/bba epoch review-items \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --evidence-root .bba
```

The reviewer uses only the candidate's `solver_bundle` directory.
The reviewer reconstructs all six answers.
Save the answers as one JSON object:

```json
{
  "ITEM_ID_1": "ANSWER_1",
  "ITEM_ID_2": "ANSWER_2"
}
```

The real file must contain all six selected IDs.
Save the six construct-validity findings as one JSON object:

```json
{
  "named_capability_valid": true,
  "public_materials_sufficient": true,
  "oracle_consistent": true,
  "scorer_consistent": true,
  "no_arbitrary_obscurity": true,
  "useful_evaluation": true
}
```

Keep the Ed25519 private-key file outside the evidence root.
The public-key file is not secret.

Record the review:

```bash
.venv/bin/bba epoch record-review \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --reviewer-id REVIEWER_ID \
  --answers reviewer-answers.json \
  --findings reviewer-findings.json \
  --decision approved \
  --limitation "LIMITATION TEXT" \
  --key-id REVIEWER_KEY_ID \
  --signing-key-file /protected/path/reviewer.key \
  --public-key-file reviewer-public-key.pem \
  --evidence-root .bba
```

An approval fails if one answer or one required finding is incorrect.
The command writes a signed epoch record and an append-only registry record.

## 13. Freeze the public audit population

BBA builds the public evaluator profiles from stored evidence.
BBA also builds the matched damage profiles and the public-optimizer control.
The operator does not prepare a score file.

Freeze these public values:

```bash
.venv/bin/bba epoch freeze-audit \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Do not reveal the holdout before this command and public closure are complete.

## 14. Close the public epoch

Run this command:

```bash
.venv/bin/bba epoch close \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

The command writes the matrix, candidate status, creator ranks, solver ranks, and adaptation values.
It does not include hidden evidence.
The phase becomes `public_closed`.

## 15. Run the sealed audit

BBA can now open this file:

```text
.bba/epochs/EPOCH_ID/private/holdout-plan.json
```

The file matches all commitments in the manifest.

Run the audit:

```bash
.venv/bin/bba epoch audit \
  --epoch-id EPOCH_ID \
  --evidence-root .bba
```

Check the audit status and every component value.
Do not use only the combined BBB value.
BBA generates the fresh instances and runs the committed hidden panel.
BBA derives both target vectors from stored evidence.
BBA retires the revealed holdout after this command.

## 16. Local files

The evidence root has this layout:

```text
bba-state.sqlite3
locks/
epochs/
  EPOCH_ID/
    manifest.json
    private/
      holdout-plan.json
    candidates/
    round-seeds/
    instances/
    validations/
    solver-cells/
    agent-traces/
    promotions/
    evaluation/
    audit/
    state/
registries/
  canonical-benchmarks/
```

Do not edit an evidence file or the SQLite file.
Do not put a signing key in this directory.
Protect the evidence root because it contains sealed holdout material.
Back up the complete directory after epoch creation, each public run, and each audit.
Exclude `private/` when you publish public evidence.

## 17. Failure response

Use these rules after a failure:

- Keep all published evidence.
- Inspect `epoch status`.
- Correct the local or provider fault.
- Run the same command again.
- Do not convert a timeout or provider error to a zero score.
- Do not close an epoch with an incomplete solver panel.
- Do not reveal hidden evidence before public closure.
- Do not reuse revealed holdout material in another epoch.

## 18. Cost controls

BBA uses serverless model inference.
It has no persistent model-serving charge.

Use these controls:

- Set a Google Cloud budget and alerts.
- Set model quotas before the epoch.
- Check local agent traces after a small test epoch.
- Estimate the full epoch from measured token use.
- Keep three solver repetitions for a conforming version 5 epoch.

Local storage, local CPU work, and local backup have no Vertex AI inference charge.
