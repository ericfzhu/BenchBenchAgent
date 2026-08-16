# Local development portal

The BBA development portal is the normal browser interface for local setup,
testing, epoch operation, review, and inspection. It binds only to
`127.0.0.1` and uses the same controller, SQLite state, and immutable evidence
as the CLI.

Start it from an installed checkout:

```bash
.venv/bin/bba web --evidence-root .bba
```

Open `http://127.0.0.1:8765`.

## Workspace

The workspace landing page shows four readiness areas before paid work:

- Ubuntu/macOS generated-code sandbox availability
- Google Application Default Credentials and selected project
- Frozen price coverage for every serverless catalog route
- Candidate dependency policy and approved local wheels

It also exposes three serialized local diagnostics:

- Check the active sandbox
- Inspect the frozen model catalog
- Run the complete local unit-test suite

Diagnostics share the same one-operation queue as epoch mutations. This avoids
running a test suite and changing an epoch at the same time.

## Epoch workflow

Each epoch page presents the required order as a seven-step workflow:

1. Setup
2. Paid preflight
3. Public tournament
4. Human review
5. Freeze audit inputs
6. Publish results
7. Sealed audit

Only operations that are valid for the current phase are enabled. An active
operation disables other changes until it completes. Failed controller work is
shown directly on the epoch page with its work ID and saved error.

The page also displays snapshots, solver cells, model-call use, and the
controller's conservative USD estimate against the frozen hard limit.

## Candidate review

Final-round candidates have a two-step review page:

1. Record independent solvability evidence.
2. Record a signed human decision.

The page becomes read-only as soon as the public audit population is frozen.
The evidence layer enforces the same boundary, so a direct CLI or HTTP retry
cannot add late review inputs.

## Security boundary

The portal retains the existing protections:

- IPv4 loopback binding
- Trusted host and origin checks
- Per-process form token checks
- Browser frame denial and a restrictive content-security policy
- One serialized change operation at a time

Do not expose the portal through a reverse proxy, tunnel, container port, or
non-loopback network interface.
