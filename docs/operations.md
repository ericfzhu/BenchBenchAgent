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
- Access to each selected serverless Model Garden model
- A supported local operating-system sandbox
- Sufficient local disk space
- An independent reviewer
- Sealed holdout material

Google documents the current Vertex AI setup in the [Vertex AI quickstart](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart).

## 3. Install BBA

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
Open each selected model card in Model Garden.
Accept the model terms when Google Cloud requests this action.

The model card must show `Serverless`.
Do not select a card that shows `Self-deployed`.
Model availability, quota, price, and terms can change.
Check each card before each epoch.

## 5. Authenticate the local process

Create local Application Default Credentials:

```bash
gcloud auth application-default login
```

Set the required environment values:

```bash
export GOOGLE_CLOUD_PROJECT="PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="global"
export GOOGLE_GENAI_USE_ENTERPRISE="TRUE"
```

Do not put a credential file in the evidence root or a candidate workspace.
Generated code cannot read the local credentials.

## 6. Check the local sandbox

Run this command:

```bash
.venv/bin/bba sandbox-status
```

On macOS, the result must show `macos-seatbelt` and `available: true`.
Set `sandbox.backend` in the manifest to the reported backend.
BBA stops before inference if the required sandbox is not available.

The sandbox has no network access.
The sandbox cannot read controller credentials, other candidates, or holdout files.

## 7. Prepare the manifest

Copy the example:

```bash
cp examples/serverless-pilot-manifest.json epoch-manifest.json
```

Change these values:

- `epoch_id`
- `gcp_project`
- `public_seed`
- Model IDs, families, and reasoning levels
- Resource limits
- Decision limits
- Evaluator version
- Sandbox backend
- All hidden commitments

Select at least four model configurations from at least three model families.
Use only serverless Vertex AI model IDs.

The example contains zero-value hidden commitments.
The `epoch create` command rejects these values.

## 8. Prepare hidden commitments

The audit authority must prepare these objects before epoch creation:

- Hidden solver panel
- Hidden seeds
- Audit policy

Keep the objects in a local protected directory that is outside the evidence root.
Do not give these objects to the creator process.
Put only their SHA-256 commitments in the manifest.

Calculate each commitment from canonical JSON:

```python
from bba.protocol import digest_json

hidden_material = {
    "hidden_solver_panel": ["SEALED_MODEL_CONFIGURATION"],
    "hidden_seeds": [991, 997],
    "audit_policy": {"version": "audit-v1"},
}

for name, value in hidden_material.items():
    print(name, digest_json(value))
```

Do not reveal `hidden_material` before public closure.

## 9. Create the local epoch

Use one evidence root for all local BBA data.
The default root is `.bba`.

```bash
.venv/bin/bba epoch create \
  --manifest epoch-manifest.json \
  --evidence-root .bba
```

This command validates and freezes the manifest.
It also creates the local SQLite state file.
You cannot change the manifest after this command.

## 10. Run or resume the public tournament

Run this command:

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
5. It validates each new snapshot in the local sandbox.
6. It runs unfinished solver cells through Vertex AI.
7. It saves each result before it starts the next work item.

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
Keep the signing-key file outside the evidence root.

Record the review:

```bash
.venv/bin/bba epoch record-review \
  --epoch-id EPOCH_ID \
  --snapshot-id SNAPSHOT_ID \
  --reviewer-id REVIEWER_ID \
  --answers reviewer-answers.json \
  --decision approved \
  --limitation "LIMITATION TEXT" \
  --key-id REVIEWER_KEY_ID \
  --signing-key-file /protected/path/reviewer.key \
  --evidence-root .bba
```

An approval fails if one answer is incorrect.
The command writes a signed epoch record and an append-only registry record.

## 13. Freeze the public audit population

The audit authority prepares the public evaluator profiles before it opens the holdout.
The score file is a JSON object with normalized values:

```json
{
  "base-profile": 0.90,
  "damaged-profile": 0.20,
  "public-optimizer": 0.99
}
```

The defect-pair file is a JSON array:

```json
[
  {
    "base_id": "base-profile",
    "damaged_id": "damaged-profile",
    "category": "controlled_damage"
  }
]
```

Freeze these public values:

```bash
.venv/bin/bba epoch freeze-audit \
  --epoch-id EPOCH_ID \
  --public-scores public-scores.json \
  --defect-pairs defect-pairs.json \
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

The audit authority now releases the committed material.
The released JSON object must match all manifest commitments.

Prepare two JSON score objects:

- The composite holdout scores
- The hidden-only holdout scores

Run the audit:

```bash
.venv/bin/bba epoch audit \
  --epoch-id EPOCH_ID \
  --composite-holdout composite-holdout.json \
  --hidden-only-holdout hidden-only-holdout.json \
  --revealed-material revealed-material.json \
  --evidence-root .bba
```

Check the audit status and every component value.
Do not use only the combined BBB value.
Retire the revealed holdout after this command.

## 16. Local files

The evidence root has this layout:

```text
bba-state.sqlite3
locks/
epochs/
  EPOCH_ID/
    manifest.json
    candidates/
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
Do not put holdout material or signing keys in this directory.
Back up the complete directory after each public run and after each audit.

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
- Start with the pilot token and call limits.
- Check local agent traces after the pilot.
- Estimate the full epoch from measured token use.
- Keep three solver repetitions for a conforming version 1 epoch.

Local storage, local CPU work, and local backup have no Vertex AI inference charge.
