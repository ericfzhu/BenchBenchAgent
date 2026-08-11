# BBA operations guide

This guide tells an operator how to prepare and run the present BBA implementation.
Read the [protocol specification](protocol.md) before you run a paid epoch.
Google Python Agent Development Kit (ADK) supplies the model agent runtime.

## 1. Current operating limit

The present command-line interface can inspect the sandbox and validate one package.
It cannot run or resume a complete live epoch.

You can run a live public epoch with the Python API.
You must keep the Python process alive through the public run, human review, public closure, and holdout audit.
If the process stops, the controller cannot restore its live state from the evidence files.

Use the end-to-end test for a safe local demonstration.
Do not use the present controller for an unattended production epoch.

## 2. Required services

Prepare these items:

- Python 3.10 or later
- Google Cloud CLI
- A Google Cloud project with billing
- Vertex AI API
- Access to each selected Model Garden model
- A supported operating-system sandbox
- Storage for immutable evidence
- An independent reviewer
- Sealed holdout material

Google documents the current Vertex AI setup in the [Vertex AI quickstart](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/quickstart).
Google documents the Cloud Run boundary in [Code execution in Cloud Run](https://docs.cloud.google.com/run/docs/code-execution).

## 3. Install BBA

Create a virtual environment:

```bash
python3.10 -m venv .venv
```

Install BBA:

```bash
.venv/bin/pip install -e .
```

Confirm the installed ADK version:

```bash
.venv/bin/python -c "import google.adk; print(google.adk.__version__)"
```

The command must print `2.6.3`.

Run all tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 4. Prepare Google Cloud

Select the project:

```bash
gcloud config set project PROJECT_ID
```

Enable the Vertex AI API:

```bash
gcloud services enable aiplatform.googleapis.com
```

The person who enables the API needs `roles/serviceusage.serviceUsageAdmin` or an equivalent custom role.

Give the BBA operator or service account `roles/aiplatform.user`.
Use a narrower custom role if your organization requires one.

Open each partner model card in Model Garden.
Accept the model terms if Google Cloud asks for acceptance.
Confirm that the card shows `Serverless`.
Do not select a card that shows `Self-deployed`.

An organization policy can deny a Model Garden model.
Check the policy if a permitted model request fails.
See [Control access to Model Garden models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/control-model-access).

## 5. Authenticate

For local development, create Application Default Credentials:

```bash
gcloud auth application-default login
```

Set the runtime environment:

```bash
export GOOGLE_CLOUD_PROJECT="PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="global"
export GOOGLE_GENAI_USE_ENTERPRISE="TRUE"
```

For a hosted controller, use a service account.
Do not put a credential file in a candidate workspace.
Do not put a credential in the controller image.

## 6. Select serverless models

Select at least four configurations from at least three model families.
Confirm that each model supports function calls and token-use metadata.

The pilot template uses these Model Garden entries:

| Publisher | Model card | BBA model value | Family |
| --- | --- | --- | --- |
| Google | Gemini 3.6 Flash | `gemini-3.6-flash` | `gemini` |
| Google | Gemini 3.5 Flash Lite | `gemini-3.5-flash-lite` | `gemini` |
| Anthropic | Claude Sonnet 5 | `claude-sonnet-5@default` | `claude` |
| xAI | Grok 4.3 | `xai/grok-4.3` | `grok` |

Model availability, price, quota, and terms can change.
Check all four cards before each epoch.

BBA sends Google and Anthropic model IDs through the native ADK registry.
BBA sends other Model Garden serverless IDs through the ADK `LiteLlm` connector and the `vertex_ai/` route.

## 7. Prepare the sandbox

### Local macOS sandbox

BBA uses Seatbelt for local development on macOS.
Check the boundary:

```bash
.venv/bin/python -m bba.cli sandbox-status
```

The output must show `macos-seatbelt` and `available: true`.
Set `sandbox.backend` to `macos-seatbelt` in the manifest.

### Cloud Run sandbox

Use the Cloud Run sandbox launcher for a hosted worker.
Enable the launcher on the service:

```bash
gcloud beta run services update SERVICE \
  --region REGION \
  --sandbox-launcher
```

The sandbox launcher is a Preview feature.
Its interface can change.

Set `sandbox.backend` to `gcp-cloud-run` in the manifest.
Use one active epoch worker on each service instance.
Do not put credentials, holdout data, or other candidates in the worker file system.

## 8. Prepare the manifest

Copy the template:

```bash
cp examples/serverless-pilot-manifest.json /secure/path/epoch-manifest.json
```

Change these values:

- `epoch_id`
- `gcp_project`
- `public_seed`
- Each model ID and reasoning level
- All resource limits
- All decision limits
- The evaluator version
- The sandbox backend
- All hidden commitments

Do not change the protocol version for a normal version 1 epoch.

The template prompt digests match the instructions in BBA 0.4.
Calculate the digests again if you change an instruction.

## 9. Prepare hidden commitments

The audit authority must prepare these objects before the public run:

- Hidden solver panel
- Hidden seeds
- Audit policy

Keep the objects outside the controller worker.
Give the controller only the SHA-256 commitment for each object.

BBA calculates a commitment from canonical JSON.
Use this Python example in the secure audit environment:

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

Replace all placeholder commitments in the template.
Do not publish `hidden_material` before public closure.

## 10. Load the manifest in Python

The package does not contain a JSON manifest loader.
Use this code in the long-lived epoch process:

```python
import json
from pathlib import Path

from bba.protocol import (
    DecisionThresholds,
    ExperimentManifest,
    ModelIdentity,
    ResourceBudget,
    SandboxCapabilities,
)

path = Path("/secure/path/epoch-manifest.json")
data = json.loads(path.read_text(encoding="utf-8"))

if any(value == "0" * 64 for value in data["hidden_commitments"].values()):
    raise ValueError("replace all example hidden commitments")

data["cohort"] = tuple(
    ModelIdentity(
        **{
            **item,
            "tools": tuple(item.get("tools", ())),
        }
    )
    for item in data["cohort"]
)
data["thresholds"] = DecisionThresholds(**data["thresholds"])
data["budget"] = ResourceBudget(**data["budget"])
data["sandbox"] = SandboxCapabilities(**data["sandbox"])
manifest = ExperimentManifest(**data)
```

The loader rejects the example commitment values.
Replace them before you start the process.

## 11. Run a public epoch with the Python API

Use one long-lived Python process.
Do not stop the process until the holdout audit is complete.

```python
from pathlib import Path

from bba.adk_runtime import build_adk_backends
from bba.evidence import EvidenceStore
from bba.runtime import SecureSandbox
from bba.tournament import TournamentController
from bba.validator import PackageValidator

sandbox = SecureSandbox()
creators, solvers = build_adk_backends(
    manifest,
    construction_sandbox=sandbox,
)

controller = TournamentController(
    manifest=manifest,
    evidence=EvidenceStore(Path("/secure/evidence")),
    validator=PackageValidator(
        sandbox,
        sample_count=manifest.thresholds.sample_count,
    ),
    creator_backends=creators,
    solver_backends=solvers,
)

controller.run_public_epoch()
```

This call runs three rounds.
It validates each snapshot before it starts solver cells.
It writes immutable evidence during the run.

Monitor model quotas and billed token use in Google Cloud.
Stop the process if evidence publication fails.
Do not delete a partial evidence directory.

## 12. Record human reviews

Review final-round candidates after the public run.
For each candidate, get the six selected item IDs:

```python
selected_ids = controller.select_review_items(snapshot)
```

Give the reviewer only the public solver bundle and the selected IDs.
The reviewer must reconstruct each answer.

Store the signing key in a secret manager or another approved secret store.
Do not write the key to the evidence directory.

Record the decision:

```python
from bba.protocol import PromotionDecision

controller.record_human_review(
    snapshot=snapshot,
    reviewer_id="REVIEWER_ID",
    reconstructed_answers=reviewer_answers,
    decision=PromotionDecision.APPROVED,
    limitations=("REVIEWER_LIMITATION",),
    key_id="REVIEWER_KEY_ID",
    signing_key=reviewer_signing_key,
)
```

Do not approve a candidate if the reviewer cannot reconstruct all six answers.

## 13. Freeze and close the public epoch

Prepare the public evaluator scores and controlled damage pairs.
Freeze them before you reveal hidden evidence:

```python
from bba.audit import DefectPair

controller.freeze_audit_population(
    public_scores=public_scores,
    defect_pairs=(
        DefectPair("BASE_PROFILE", "DAMAGED_PROFILE", "DAMAGE_CATEGORY"),
    ),
)
```

Close the public epoch:

```python
public_record = controller.close_public_epoch()
```

Confirm that `hidden_evidence_included` is `false`.
Do not reveal the holdout before this record exists.

## 14. Run the holdout audit

The audit authority now releases the committed material.
The released objects must match the prior commitments.

Run the audit:

```python
audit_record = controller.run_holdout_audit(
    composite_holdout=composite_scores,
    hidden_only_holdout=hidden_only_scores,
    revealed_material=hidden_material,
)
```

Check the audit status and every component value.
Do not use only the combined BBB value.
Retire the revealed holdout after this call.

## 15. Inspect the output

The evidence root has this layout:

```text
epochs/
  EPOCH_ID/
    manifest.json
    candidates/
    validations/
    solver-cells/
    agent-traces/
    evaluation/
    audit/
    state/
registries/
  canonical-benchmarks/
```

Do not edit an evidence file.
A repeated write to an immutable path must fail.

## 16. Failure response

Use these rules after a failure:

- Keep all published evidence.
- Record the model, candidate, round, and cell state.
- Do not convert a provider error or timeout to a zero score.
- Do not rank a candidate with an incomplete solver panel.
- Do not reuse an epoch ID after a partial run.
- Start a new epoch with a new ID after you correct the cause.
- Do not reveal hidden evidence for an incomplete public epoch.

## 17. Cost controls

BBA uses serverless model requests only.
It has no persistent model-serving charge.

Use these controls:

- Set a Google Cloud budget and alerts.
- Set model quotas before the epoch.
- Use the pilot resource limits before you use the default limits.
- Check token-use traces after the pilot.
- Estimate the full epoch from measured input and output tokens.
- Keep three repetitions for a conforming public epoch.

The pilot template limits each invocation to 8,000 total tokens and 16 model calls.
The limit is not a price guarantee.
Each provider has separate input and output prices.

## 18. Production readiness check

Do not call the present controller restart-safe.
Production operation still needs these features:

- A versioned JSON manifest loader
- A restart-safe epoch state loader
- A `run-epoch` command
- Separate review, closure, and audit commands
- Idempotent retry controls
- Tested Cloud Run deployment files

The deterministic test proves the protocol flow.
It does not prove production recovery.
