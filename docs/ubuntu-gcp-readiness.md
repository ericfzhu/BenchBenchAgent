# Ubuntu and Google Cloud readiness

Use this checklist before creating a paid BenchBenchAgent epoch on Ubuntu.
The checks separate local installation, the generated-code sandbox, Application
Default Credentials, and catalog model access so a failure identifies the
correct subsystem.

## 1. Install the local runtime

BBA supports Python 3.10 or later. Use the Ubuntu-provided `python3` unless a
specific interpreter has been installed intentionally.

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

The command must print `2.6.3`.

## 2. Authenticate the Python process

`gcloud auth login` authenticates the Cloud SDK. BBA and its Python libraries
use Application Default Credentials instead.

```bash
PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"

test -n "$PROJECT_ID"
test "$PROJECT_ID" != "(unset)"

gcloud auth application-default login
gcloud auth application-default set-quota-project "$PROJECT_ID"

export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
```

On a machine without a browser, use:

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

BBA propagates the frozen project and location to both environment-variable
families used by its model adapters:

- `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`
- `VERTEXAI_PROJECT` and `VERTEXAI_LOCATION`

The frozen catalog currently requires the `global` Vertex location.

## 3. Enable Vertex AI

```bash
gcloud services enable aiplatform.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"
```

The operator project must have billing, suitable Vertex AI permissions, and
access to every model in the frozen catalog. Preview or partner models may have
project-specific terms or quota requirements. BBA does not silently omit a
model: the paid preflight records a result for every catalog identity and fails
until the complete panel passes.

## 4. Verify Bubblewrap

```bash
.venv/bin/bba sandbox-status
```

On Ubuntu the result must contain:

```json
{
  "available": true,
  "backend": "linux-bubblewrap",
  "expected_backend": "linux-bubblewrap"
}
```

If Bubblewrap is installed but the namespace probe fails, inspect AppArmor and
kernel messages:

```bash
sudo journalctl -k -b | \
  grep -E 'apparmor.*(DENIED|userns_create)|comm="bwrap"' | \
  tail -n 100
```

On Ubuntu releases that ship the extra Bubblewrap profile, load it with:

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

Do not globally disable the host's unprivileged-user-namespace protections as a
first response. BBA relies on the sandbox failing closed.

## 5. Run local checks

```bash
.venv/bin/python -m unittest discover \
  -s tests \
  -p 'test_*.py' \
  -v
```

A skipped security suite is not Ubuntu sandbox evidence. Confirm that the
security tests actually ran on the target host.

The committed generated-code wheel catalog is empty. Candidate packages must
therefore use the Python standard library and an empty or comment-only
`requirements.lock` until approved wheels are added to the catalog.

## 6. Create and preflight a smoke epoch

```bash
EPOCH_ID="smoke-$(date -u +%Y%m%dT%H%M%SZ)"

.venv/bin/bba epoch create \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba

.venv/bin/bba epoch preflight \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba
```

Preflight failures are saved under `preflight-attempts/` so the operator can
correct access, quota, credentials, or local configuration and retry. Only a
complete passing result is frozen as `preflight/vertex.json`; `epoch run`
requires that passing record to match the frozen manifest.

For a full traceback during diagnosis:

```bash
BBA_DEBUG=1 .venv/bin/bba epoch preflight \
  --epoch-id "$EPOCH_ID" \
  --evidence-root .bba
```

Do not start the full tournament until the sandbox, tests, and every catalog
preflight entry pass and the operator has reviewed the invocation and token
limits.
