# Ubuntu and Google Cloud readiness

Use this checklist before creating paid BenchBenchAgent work on Ubuntu. It separates installation, generated-code sandboxing, Application Default Credentials, price coverage, and catalog model access so failures point to the correct subsystem.

## 1. Install the local runtime

BBA supports Python 3.10 or later. Use the Ubuntu-provided `python3` unless another interpreter was selected intentionally.

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

## 2. Authenticate the Python process

`gcloud auth login` authenticates the Cloud SDK. BBA and its Python libraries use Application Default Credentials instead.

```bash
PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"

test -n "$PROJECT_ID"
test "$PROJECT_ID" != "(unset)"

gcloud auth application-default login
gcloud auth application-default set-quota-project "$PROJECT_ID"
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
```

On a headless host:

```bash
gcloud auth application-default login --no-launch-browser
```

Verify the credentials and project visible to Python:

```bash
.venv/bin/python - <<'PY'
import google.auth

credentials, project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
print("credential type:", type(credentials).__name__)
print("ADC project:", project)
print("quota project:", getattr(credentials, "quota_project_id", None))
if not project:
    raise SystemExit("ADC did not identify a project")
PY
```

BBA propagates the frozen project/location to both adapter variable families:

- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`;
- `VERTEXAI_PROJECT` and `VERTEXAI_LOCATION`.

The current catalog uses the Vertex `global` location.

## 3. Enable Vertex AI and partner access

```bash
gcloud services enable aiplatform.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"
```

The project must have billing, suitable Vertex AI permissions, accepted terms, and sufficient quota for every frozen model route. Preview and partner models can have additional project-specific access requirements.

BBA never silently drops an unavailable route. Paid preflight returns a result for every frozen identity and fails until the complete panel passes.

## 4. Verify Bubblewrap

```bash
.venv/bin/bba sandbox-status
```

On Ubuntu the result must include:

```json
{
  "available": true,
  "backend": "linux-bubblewrap",
  "expected_backend": "linux-bubblewrap"
}
```

If Bubblewrap is installed but namespaces fail, inspect AppArmor and kernel messages:

```bash
sudo journalctl -k -b | \
  grep -E 'apparmor.*(DENIED|userns_create)|comm="bwrap"' | \
  tail -n 100
```

On Ubuntu releases that ship the extra Bubblewrap profile:

```bash
if [ -f /usr/share/apparmor/extra-profiles/bwrap-userns-restrict ] && \
   [ ! -f /etc/apparmor.d/bwrap-userns-restrict ]; then
  sudo install -m 0644 \
    /usr/share/apparmor/extra-profiles/bwrap-userns-restrict \
    /etc/apparmor.d/bwrap-userns-restrict
fi

if [ -f /etc/apparmor.d/bwrap-userns-restrict ]; then
  sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
fi
```

Do not globally disable unprivileged-user-namespace protections as a first response. BBA relies on the sandbox failing closed.

The sandbox backend becomes part of the immutable epoch manifest. Public validation, audit-population generation, and sealed audit all require an available backend matching that frozen value.

## 5. Run local checks

```bash
.venv/bin/python -m unittest discover \
  -s tests \
  -p 'test_*.py' \
  -v
```

A skipped security suite is not Ubuntu sandbox evidence. Confirm the target-host security tests actually run.

The generated-code wheel catalog is currently empty. Candidate packages must use the Python standard library and an empty or comment-only `requirements.lock` until approved wheels are added.

## 6. Check the local development portal

Start the portal:

```bash
.venv/bin/bba web --evidence-root .bba
```

Open `http://127.0.0.1:8765`.

Before paid work, the workspace readiness panel should report ready for:

- sandbox;
- ADC/project;
- frozen price coverage;
- candidate dependency policy.

The portal can also serialize the sandbox check, catalog inspection, and complete local unit suite through its local operation queue.

## 7. Verify price coverage and cost limits

The current price catalog must contain every frozen public route. BBA uses the catalog for a retry-inclusive preflight estimate and for runtime conservative USD reservations.

Runtime reservations apply a frozen two-times safety factor and stop before a new model attempt can exceed the epoch's `max_estimated_cost_usd` ceiling. Calls and input/output tokens are enforced independently.

Use Google Cloud budgets and alerts as a second, external cost control; BBA's local accounting is not a replacement for billing alerts.

## 8. Create and preflight a smoke epoch

```bash
EPOCH_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"

.venv/bin/bba epoch create \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba

.venv/bin/bba epoch preflight \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba
```

Preflight failures are saved under `preflight-attempts/` so access, quota, credentials, or local configuration can be corrected and retried. Only a complete passing result is frozen as `preflight/vertex.json`, and `epoch run` requires that passing record to match the manifest.

For a full traceback:

```bash
BBA_DEBUG=1 .venv/bin/bba epoch preflight \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba
```

Do not start the full tournament until:

- Bubblewrap and the target-host security suite pass;
- portal/local readiness is green;
- every catalog route passes paid preflight;
- the retry-inclusive cost estimate is below the frozen hard ceiling;
- the operator has reviewed Google Cloud quotas and billing alerts.

## 9. Production verification remains separate

A successful smoke preflight does not make the repository production-verified. Production acceptance still requires a complete paid epoch, controlled creator/public-solver/hidden-solver interruption and resume, independent signed human review, public closure, sealed audit, and replay of every successful live attempt.

See [Production acceptance](production-acceptance.md).
