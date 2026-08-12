"""Localhost-only web console for BBA operators and reviewers."""

from __future__ import annotations

import html
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import quote, urlparse

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from bba.epoch_setup import new_epoch_id
from bba.operator import OperatorConsole
from bba.protocol import PromotionDecision, SolvabilityCertificateType


CSS = """
:root {
  color-scheme: light;
  --paper: #f4f1ea;
  --surface: #fffdf8;
  --ink: #17212b;
  --muted: #66717c;
  --line: rgba(23, 33, 43, 0.12);
  --blue: #145ea8;
  --blue-dark: #0d477f;
  --green: #16724a;
  --amber: #9a5d05;
  --red: #a73535;
  --shadow: 0 0 0 1px rgba(0,0,0,.06), 0 1px 2px -1px rgba(0,0,0,.06), 0 8px 28px rgba(36,42,48,.06);
  --shadow-hover: 0 0 0 1px rgba(0,0,0,.08), 0 1px 2px -1px rgba(0,0,0,.08), 0 10px 32px rgba(36,42,48,.09);
}
* { box-sizing: border-box; }
html { -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
body { margin: 0; background: var(--paper); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: var(--blue); text-decoration: none; }
a:hover { color: var(--blue-dark); text-decoration: underline; }
h1, h2, h3 { margin: 0; letter-spacing: -.025em; text-wrap: balance; }
p, li, label, td, th { text-wrap: pretty; }
.shell { min-height: 100vh; display: grid; grid-template-columns: 244px minmax(0, 1fr); }
.side { background: #17212b; color: #f7f4ed; padding: 28px 22px; display: flex; flex-direction: column; gap: 28px; }
.brand { color: #fff; font-size: 21px; font-weight: 760; letter-spacing: -.03em; }
.brand small { color: #9fb0bf; display: block; font-size: 11px; font-weight: 650; letter-spacing: .12em; margin-top: 5px; text-transform: uppercase; }
.side nav { display: grid; gap: 5px; }
.side nav a { color: #c9d3dc; min-height: 44px; display: flex; align-items: center; padding: 0 12px; border-radius: 8px; transition-property: background-color, color; transition-duration: 150ms; }
.side nav a:hover, .side nav a.current { background: rgba(255,255,255,.09); color: #fff; text-decoration: none; }
.local-note { margin-top: auto; color: #9fb0bf; font-size: 12px; line-height: 1.55; }
.local-dot { width: 8px; height: 8px; display: inline-block; border-radius: 50%; background: #5fd39b; box-shadow: 0 0 0 4px rgba(95,211,155,.12); margin-right: 8px; }
.main { min-width: 0; padding: 42px clamp(24px, 5vw, 72px) 72px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 30px; }
.eyebrow { color: var(--blue); font-size: 11px; font-weight: 760; letter-spacing: .13em; margin-bottom: 8px; text-transform: uppercase; }
.subtitle { color: var(--muted); line-height: 1.65; margin: 10px 0 0; max-width: 720px; }
.grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 18px; }
.card { background: var(--surface); border-radius: 14px; box-shadow: var(--shadow); padding: 22px; transition-property: box-shadow, transform; transition-duration: 150ms; transition-timing-function: ease-out; }
.card:hover { box-shadow: var(--shadow-hover); }
.span-4 { grid-column: span 4; } .span-5 { grid-column: span 5; } .span-7 { grid-column: span 7; } .span-8 { grid-column: span 8; } .span-12 { grid-column: span 12; }
.metric-label { color: var(--muted); font-size: 12px; font-weight: 680; letter-spacing: .05em; text-transform: uppercase; }
.metric { font-size: 30px; font-weight: 730; margin-top: 9px; font-variant-numeric: tabular-nums; }
.section { margin-top: 32px; }
.section-head { align-items: center; display: flex; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.section-head h2 { font-size: 19px; }
.table-wrap { overflow: auto; background: var(--surface); border-radius: 14px; box-shadow: var(--shadow); }
table { border-collapse: collapse; min-width: 680px; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 14px 16px; text-align: left; vertical-align: middle; }
th { color: var(--muted); font-size: 11px; font-weight: 760; letter-spacing: .08em; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
tbody tr { transition-property: background-color; transition-duration: 150ms; }
tbody tr:hover { background: rgba(20,94,168,.035); }
.num { font-variant-numeric: tabular-nums; text-align: right; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .88em; }
.muted { color: var(--muted); }
.chip { align-items: center; border-radius: 999px; display: inline-flex; font-size: 11px; font-weight: 730; min-height: 26px; padding: 3px 9px; white-space: nowrap; }
.chip.green { background: #e7f5ed; color: var(--green); }
.chip.blue { background: #e6f0fa; color: var(--blue-dark); }
.chip.amber { background: #fbefda; color: var(--amber); }
.chip.red { background: #fae8e7; color: var(--red); }
.chip.gray { background: #ececea; color: #59616a; }
.button { align-items: center; background: var(--blue); border: 0; border-radius: 9px; box-shadow: 0 1px 2px rgba(0,0,0,.14); color: #fff; cursor: pointer; display: inline-flex; font: inherit; font-size: 13px; font-weight: 690; justify-content: center; min-height: 44px; padding: 0 16px; transition-property: scale, background-color, box-shadow; transition-duration: 150ms; }
.button:hover { background: var(--blue-dark); color: #fff; text-decoration: none; }
.button:active { scale: .96; }
.button.secondary { background: var(--surface); box-shadow: var(--shadow); color: var(--ink); }
.button.secondary:hover { background: #f8f7f2; box-shadow: var(--shadow-hover); }
.button.danger { background: var(--red); }
.button.danger:hover { background: #882929; }
.button[disabled] { cursor: not-allowed; opacity: .5; }
.button-row { display: flex; flex-wrap: wrap; gap: 10px; }
form.stack { display: grid; gap: 15px; }
.field { display: grid; gap: 7px; }
.field > label, fieldset > legend { font-size: 12px; font-weight: 720; }
input, select, textarea { background: #fff; border: 1px solid rgba(23,33,43,.22); border-radius: 8px; color: var(--ink); font: inherit; font-size: 14px; min-height: 44px; outline: none; padding: 10px 12px; transition-property: border-color, box-shadow; transition-duration: 150ms; width: 100%; }
textarea { min-height: 96px; resize: vertical; }
input:focus, select:focus, textarea:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(20,94,168,.14); }
.check { align-items: flex-start; display: flex; gap: 10px; min-height: 40px; padding: 8px 0; }
.check input { flex: 0 0 20px; min-height: 20px; width: 20px; }
fieldset { border: 0; margin: 0; padding: 0; }
.hint { color: var(--muted); font-size: 12px; line-height: 1.55; margin: 0; }
.notice { border-left: 3px solid var(--blue); background: rgba(20,94,168,.07); border-radius: 0 8px 8px 0; padding: 13px 15px; }
.notice.warn { border-color: var(--amber); background: rgba(154,93,5,.08); }
.notice.error { border-color: var(--red); background: rgba(167,53,53,.07); }
.empty { color: var(--muted); padding: 42px 24px; text-align: center; }
.job-output { background: #111921; border-radius: 9px; color: #d7e0e8; max-height: 440px; overflow: auto; padding: 16px; white-space: pre-wrap; }
.matrix td, .matrix th { font-size: 12px; min-width: 104px; }
.matrix th:first-child, .matrix td:first-child { background: var(--surface); left: 0; min-width: 220px; position: sticky; z-index: 1; }
.breadcrumb { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
.definition { display: grid; grid-template-columns: minmax(130px, .45fr) 1fr; margin: 0; }
.definition dt, .definition dd { border-bottom: 1px solid var(--line); margin: 0; padding: 11px 0; }
.definition dt { color: var(--muted); font-size: 12px; }
.definition dd { font-size: 13px; overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .shell { display: block; }
  .side { padding: 18px 20px; }
  .side nav { display: flex; overflow-x: auto; }
  .local-note { display: none; }
  .main { padding: 28px 18px 52px; }
  .span-4, .span-5, .span-7, .span-8 { grid-column: span 12; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; }
}
"""


FINDING_LABELS = {
    "named_capability_valid": "The named capability is valid.",
    "public_materials_sufficient": "The public materials are sufficient.",
    "oracle_consistent": "The oracle is consistent.",
    "scorer_consistent": "The scorer is consistent.",
    "no_arbitrary_obscurity": "The benchmark has no arbitrary obscurity.",
    "useful_evaluation": "The benchmark is a useful evaluation.",
    "solvability_certificate_adequate": "The solvability certificate is adequate.",
}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _u(value: Any) -> str:
    return quote(str(value), safe="")


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def _short(value: Any, length: int = 18) -> str:
    text = str(value)
    return text if len(text) <= length else f"{text[:length]}…"


def _tone(status: str) -> str:
    if status in {"active", "approved", "audited", "validated", "succeeded", "public_closed"}:
        return "green"
    if status in {"running", "public_running", "awaiting_review", "queued", "audit_population_frozen"}:
        return "blue"
    if status in {"failed", "invalid", "rejected", "unvalidated", "unreadable"}:
        return "red"
    if status in {"solvability_audit", "escalated", "too_easy"}:
        return "amber"
    return "gray"


def _chip(status: str) -> str:
    return f'<span class="chip {_tone(status)}">{_e(status.replace("_", " "))}</span>'


def _layout(title: str, body: str, *, current: str = "epochs", refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}
<title>{_e(title)} · BBA</title><style>{CSS}</style></head>
<body><div class="shell"><aside class="side"><a class="brand" href="/">BenchBenchAgent<small>Operator console</small></a>
<nav><a class="{'current' if current == 'epochs' else ''}" href="/">Epochs</a><a class="{'current' if current == 'jobs' else ''}" href="/jobs">Operations</a></nav>
<div class="local-note"><span class="local-dot"></span>Local controller<br>Bound to 127.0.0.1</div></aside><main class="main">{body}</main></div></body></html>"""


def _csrf(token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{_e(token)}">'


def _epoch_link(epoch_id: str) -> str:
    return f"/epochs/{_u(epoch_id)}"


def create_app(console: OperatorConsole) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        close = getattr(console, "close", None)
        if close is not None:
            close()

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.console = console
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return HTMLResponse("Untrusted origin", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def verify(request: Request, token: str, confirmed: Optional[str] = None) -> None:
        if not secrets.compare_digest(token, request.app.state.csrf_token):
            raise ValueError("the form token is invalid; reload the page")
        if confirmed is not None and confirmed != "yes":
            raise ValueError("confirm this operation before you continue")

    def fail(title: str, message: str, status: int = 400) -> HTMLResponse:
        body = f'<div class="page-head"><div><div class="eyebrow">Request stopped</div><h1>{_e(title)}</h1><p class="subtitle">{_e(message)}</p></div></div><a class="button secondary" href="/">Return to epochs</a>'
        return HTMLResponse(_layout(title, body), status_code=status)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> str:
        epochs = console.list_epochs()
        rows = "".join(
            f'<tr><td><a class="mono" href="{_epoch_link(item["epoch_id"])}">{_e(item["epoch_id"])}</a></td>'
            f'<td>{_chip(item.get("phase", "unknown"))}</td><td class="num">{_e(item.get("snapshots", "—"))}</td>'
            f'<td class="num">{_e(item.get("solver_cells", "—"))}</td><td>{_e(item.get("updated_at", item.get("error", "—")))}</td></tr>'
            for item in epochs
        ) or '<tr><td class="empty" colspan="5">No epoch exists in this evidence root.</td></tr>'
        body = f"""<div class="page-head"><div><div class="eyebrow">Local tournament control</div><h1>Epochs</h1><p class="subtitle">Start, resume, review, and inspect BBA epochs. Model inference stays on Vertex AI. State and evidence stay on this machine.</p></div></div>
<div class="grid"><section class="card span-5"><h2>Create an epoch</h2><p class="subtitle">BBA uses its frozen model catalog and the active Google Cloud project.</p><form class="stack section" method="post" action="/epochs">{_csrf(request.app.state.csrf_token)}<div class="field"><label for="epoch-id">Epoch ID</label><input id="epoch-id" name="epoch_id" value="{_e(new_epoch_id())}" pattern="[a-zA-Z0-9._-]+" required></div><button class="button" type="submit">Create epoch</button></form></section>
<section class="card span-7"><div class="metric-label">Evidence root</div><div class="mono" style="margin-top:12px;overflow-wrap:anywhere">{_e(console.evidence.root)}</div><p class="subtitle">The console does not upload this directory. Back it up as one unit.</p></section></div>
<section class="section"><div class="section-head"><h2>Saved epochs</h2><a href="/jobs">View operations</a></div><div class="table-wrap"><table><thead><tr><th>Epoch</th><th>Phase</th><th class="num">Snapshots</th><th class="num">Solver cells</th><th>Last update</th></tr></thead><tbody>{rows}</tbody></table></div></section>"""
        return _layout("Epochs", body)

    @app.post("/epochs")
    def create_epoch(request: Request, epoch_id: str = Form(...), csrf_token: str = Form(...)):
        try:
            verify(request, csrf_token)
            job = console.create_epoch(epoch_id)
            return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)
        except Exception as exc:
            return fail("Could not create the epoch", str(exc))

    @app.get("/epochs/{epoch_id}", response_class=HTMLResponse)
    def epoch_page(request: Request, epoch_id: str):
        try:
            value = console.epoch(epoch_id)
        except Exception as exc:
            return fail("Could not read the epoch", str(exc), 404)
        candidates = value["candidates"]
        candidate_rows = "".join(
            f'<tr><td><a href="{_epoch_link(epoch_id)}/candidates/{_u(item["snapshot_id"])}">{_e(item["model"])}</a><br><span class="mono muted">{_e(_short(item["snapshot_id"], 30))}</span></td>'
            f'<td class="num">{item["round"]}</td><td>{_chip(item["status"])}</td><td class="num">{item["solver_cells"]}</td>'
            f'<td class="num">{_percent(item["best_solver_median"])}</td><td class="num">{item["certificate_count"]}</td></tr>'
            for item in candidates
        ) or '<tr><td class="empty" colspan="6">The public run has not made a candidate yet.</td></tr>'
        jobs = [item for item in console.jobs.recent() if item["epoch_id"] == epoch_id]
        active_job = next((item for item in jobs if item["status"] in {"queued", "running"}), None)
        actions = "".join(
            f'<form class="card span-4 stack" method="post" action="{_epoch_link(epoch_id)}/actions/{_u(action)}">{_csrf(request.app.state.csrf_token)}<strong>{_e(label)}</strong><label class="check"><input type="checkbox" name="confirmed" value="yes" required><span>Confirm this operation</span></label><button class="button {"danger" if action == "audit" else "secondary"}" type="submit" {"disabled" if active_job else ""}>{_e(label)}</button></form>'
            for action, label in console.EPOCH_ACTIONS.items()
        )
        m = value["manifest"]
        body = f"""<div class="breadcrumb"><a href="/">Epochs</a> / {_e(epoch_id)}</div><div class="page-head"><div><div class="eyebrow">{_e(m['catalog_version'])}</div><h1>{_e(epoch_id)}</h1><p class="subtitle">Created {_e(m['created_at'])}. This epoch uses {_e(m['models'])} models, {_e(m['rounds'])} creator rounds, and {_e(m['solver_repetitions'])} solver repetitions.</p></div><div>{_chip(value['phase'])}</div></div>
<div class="grid"><section class="card span-4"><div class="metric-label">Snapshots</div><div class="metric">{value['snapshots']}</div></section><section class="card span-4"><div class="metric-label">Solver cells</div><div class="metric">{value['solver_cells']}</div></section><section class="card span-4"><div class="metric-label">Approved</div><div class="metric">{value['approved']}</div></section></div>
<section class="section"><div class="section-head"><h2>Epoch controls</h2><div class="button-row"><a class="button secondary" href="{_epoch_link(epoch_id)}/observability">View agent activity</a><a class="button secondary" href="{_epoch_link(epoch_id)}/results">View rankings</a></div></div>{'<div class="notice"><strong>An operation is active.</strong> Open the operations page for its status.</div>' if active_job else ''}<div class="grid">{actions}</div></section>
<section class="section"><div class="section-head"><h2>Candidate benchmarks</h2><span class="muted">Final-round candidates can receive certificates and signed decisions.</span></div><div class="table-wrap"><table><thead><tr><th>Candidate</th><th class="num">Round</th><th>Status</th><th class="num">Cells</th><th class="num">Best solver</th><th class="num">Certificates</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>"""
        return _layout(epoch_id, body)

    @app.get("/epochs/{epoch_id}/observability", response_class=HTMLResponse)
    def observability(epoch_id: str):
        try:
            value = console.observability(epoch_id)
        except Exception as exc:
            return fail("Could not read agent activity", str(exc), 404)
        totals = value["totals"]
        tracing = value.get("tracing", {})
        model_rows = "".join(
            f'<tr><td>{_e(row["identity"])}</td><td class="num">{row["invocations"]}</td><td class="num">{row["failures"]}</td><td class="num">{row["model_calls"]}</td><td class="num">{row["tool_calls"]}</td><td class="num">{row["total_tokens"]}</td><td class="num">{row["duration_ms"]:.0f} ms</td></tr>'
            for row in value["models"]
        ) or '<tr><td class="empty" colspan="7">No ADK invocation has started.</td></tr>'
        recent_rows = "".join(
            f'<tr><td>{_chip(row["status"])}</td><td>{_e(row["role"] or "—")}</td><td>{_e(row["identity"])}</td><td class="num">{row["model_calls"]}</td><td class="num">{row["tool_calls"]}</td><td class="num">{row["total_tokens"]}</td><td class="num">{row["duration_ms"]:.0f} ms</td><td>{_e(row["error_type"] or "—")}</td></tr>'
            for row in value["recent"]
        ) or '<tr><td class="empty" colspan="8">No ADK invocation has started.</td></tr>'
        trace_notice = (
            f'<div class="notice"><strong>Local OTLP export is on.</strong> Traces go to {_e(tracing.get("endpoint"))}. Content capture is off.</div>'
            if tracing.get("enabled")
            else f'<div class="notice warn"><strong>Local OTLP export is off.</strong> {_e(tracing.get("reason", "No endpoint is configured."))}</div>'
        )
        body = f"""<div class="breadcrumb"><a href="{_epoch_link(epoch_id)}">{_e(epoch_id)}</a> / Agent activity</div><div class="page-head"><div><div class="eyebrow">Google ADK observability</div><h1>Agent activity</h1><p class="subtitle">BBA records ADK lifecycle, usage, latency, and error metadata. It does not record prompts, tool arguments, tool results, model output, or hidden audit content.</p></div>{_chip('running' if value['active'] else 'observed')}</div>{trace_notice}
<div class="grid"><section class="card span-4"><div class="metric-label">Invocations</div><div class="metric">{totals['invocations']}</div></section><section class="card span-4"><div class="metric-label">Model calls</div><div class="metric">{totals['model_calls']}</div></section><section class="card span-4"><div class="metric-label">Total tokens</div><div class="metric">{totals['total_tokens']}</div></section></div>
<section class="section"><div class="section-head"><h2>Models</h2><span class="muted">Cumulative values for this epoch</span></div><div class="table-wrap"><table><thead><tr><th>Identity</th><th class="num">Runs</th><th class="num">Failures</th><th class="num">Model calls</th><th class="num">Tool calls</th><th class="num">Tokens</th><th class="num">Time</th></tr></thead><tbody>{model_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>Recent ADK invocations</h2><span class="muted">Refresh this page to read the latest local records.</span></div><div class="table-wrap"><table><thead><tr><th>Status</th><th>Role</th><th>Identity</th><th class="num">Model calls</th><th class="num">Tools</th><th class="num">Tokens</th><th class="num">Time</th><th>Error type</th></tr></thead><tbody>{recent_rows}</tbody></table></div></section>"""
        return _layout("Agent activity", body)

    @app.post("/epochs/{epoch_id}/actions/{action}")
    def epoch_action(request: Request, epoch_id: str, action: str, csrf_token: str = Form(...), confirmed: str = Form("")):
        try:
            verify(request, csrf_token, confirmed)
            job = console.run_epoch_action(epoch_id, action)
            return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)
        except Exception as exc:
            return fail("Could not start the operation", str(exc))

    @app.get("/epochs/{epoch_id}/candidates/{snapshot_id}", response_class=HTMLResponse)
    def candidate_page(request: Request, epoch_id: str, snapshot_id: str):
        try:
            item = console.candidate(epoch_id, snapshot_id)
        except Exception as exc:
            return fail("Could not read the candidate", str(exc), 404)
        certificates = "".join(
            f'<option value="{_e(cert["digest"])}">{_e(cert["certificate_type"])} · {_e(_short(cert["digest"]))}</option>'
            for cert in item["certificates"]
        )
        certificate_list = "".join(
            f'<li><span class="mono">{_e(cert["digest"])}</span><br><span class="muted">{_e(cert["certificate_type"])} by {_e(cert["issuer_id"])}</span></li>'
            for cert in item["certificates"]
        ) or '<li class="muted">No solvability certificate exists.</li>'
        promotion_list = "".join(
            f'<li>{_chip(record["decision"])} <span class="muted">by {_e(record["reviewer_id"])}</span></li>'
            for record in item["promotions"]
        ) or '<li class="muted">No signed review exists.</li>'
        type_options = "".join(f'<option value="{_e(value.value)}">{_e(value.value.replace("_", " "))}</option>' for value in SolvabilityCertificateType)
        decision_options = "".join(f'<option value="{_e(value.value)}">{_e(value.value)}</option>' for value in PromotionDecision)
        findings = "".join(f'<label class="check"><input type="checkbox" name="{_e(name)}" value="yes"><span>{_e(label)}</span></label>' for name, label in FINDING_LABELS.items())
        final_notice = "" if item["final_round"] else '<div class="notice warn">Only a final-round candidate can receive a certificate or canonical decision.</div>'
        disabled = "" if item["final_round"] else "disabled"
        body = f"""<div class="breadcrumb"><a href="/">Epochs</a> / <a href="{_epoch_link(epoch_id)}">{_e(epoch_id)}</a> / Candidate</div><div class="page-head"><div><div class="eyebrow">Creator round {item['round']}</div><h1>{_e(item['model'])}</h1><p class="subtitle mono">{_e(item['snapshot_id'])}</p></div>{_chip(item['status'])}</div>{final_notice}
<div class="grid section"><section class="card span-5"><h2>Evidence</h2><dl class="definition"><dt>Design digest</dt><dd class="mono">{_e(item['design_digest'])}</dd><dt>Best solver</dt><dd>{_percent(item['best_solver_median'])}</dd><dt>Panel median</dt><dd>{_percent(item['panel_median'])}</dd><dt>Solver cells</dt><dd>{item['solver_cells']}</dd><dt>Review state</dt><dd>{'Reviewed' if item['reviewed'] else 'Not reviewed'}</dd></dl><h3 class="section">Certificates</h3><ul>{certificate_list}</ul><h3 class="section">Decisions</h3><ul>{promotion_list}</ul></section>
<section class="card span-7"><h2>Record solvability evidence</h2><p class="subtitle">BBA copies each evidence file into immutable local evidence. Use an absolute local file path.</p><form class="stack section" method="post" action="{_epoch_link(epoch_id)}/candidates/{_u(snapshot_id)}/certificate">{_csrf(request.app.state.csrf_token)}<div class="field"><label>Certificate type</label><select name="certificate_type">{type_options}</select></div><div class="field"><label>Issuer ID</label><input name="issuer_id" required></div><div class="field"><label>Independence basis</label><textarea name="independence_basis" required></textarea></div><div class="field"><label>Verification method</label><textarea name="verification_method" required></textarea></div><div class="field"><label>Scope</label><input name="scope" required></div><div class="field"><label>Evidence files</label><textarea class="mono" name="evidence_lines" placeholder="working-notes.md=/absolute/path/working-notes.md" required></textarea><p class="hint">Use one NAME=/absolute/path line for each file.</p></div><div class="field"><label>Answers JSON path</label><input name="answers_path" placeholder="Required only for human reconstruction"><p class="hint">Selected item IDs: <span class="mono">{_e(', '.join(item['certificate_item_ids']) or 'not available')}</span></p></div><label class="check"><input type="checkbox" name="confirmed" value="yes" required><span>I confirm that the issuer is independent from the creator.</span></label><button class="button" type="submit" {disabled}>Record certificate</button></form></section></div>
<section class="card section"><h2>Record a signed candidate decision</h2><p class="subtitle">Approval requires every finding to pass. The approving reviewer must differ from the certificate issuer. Keep the private key outside the evidence root.</p><form class="stack section" method="post" action="{_epoch_link(epoch_id)}/candidates/{_u(snapshot_id)}/review">{_csrf(request.app.state.csrf_token)}<div class="grid"><div class="field span-4"><label>Decision</label><select name="decision">{decision_options}</select></div><div class="field span-4"><label>Reviewer ID</label><input name="reviewer_id" required></div><div class="field span-4"><label>Certificate</label><select name="certificate_digest" required><option value="">Select a certificate</option>{certificates}</select></div></div><fieldset><legend>Construct validity findings</legend><div class="grid">{findings}</div></fieldset><div class="grid"><div class="field span-4"><label>Key ID</label><input name="key_id" required></div><div class="field span-4"><label>Private key path</label><input name="signing_key_path" required></div><div class="field span-4"><label>Public key path</label><input name="public_key_path" required></div></div><div class="field"><label>Limitations</label><textarea name="limitations" placeholder="Use one limitation per line."></textarea></div><div class="field"><label>Prior escalated review digest</label><input name="prior_review_digest" placeholder="Required only for a second review"></div><label class="check"><input type="checkbox" name="confirmed" value="yes" required><span>I confirm that this decision will become immutable signed evidence.</span></label><button class="button" type="submit" {disabled}>Record signed decision</button></form></section>"""
        return _layout(f"Candidate {item['model']}", body)

    @app.post("/epochs/{epoch_id}/candidates/{snapshot_id}/certificate")
    def certificate(
        request: Request, epoch_id: str, snapshot_id: str,
        certificate_type: str = Form(...), issuer_id: str = Form(...),
        independence_basis: str = Form(...), verification_method: str = Form(...),
        scope: str = Form(...), evidence_lines: str = Form(...),
        answers_path: str = Form(""), csrf_token: str = Form(...), confirmed: str = Form(""),
    ):
        try:
            verify(request, csrf_token, confirmed)
            job = console.record_certificate(epoch_id, snapshot_id, certificate_type, issuer_id, independence_basis, verification_method, scope, evidence_lines, answers_path)
            return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)
        except Exception as exc:
            return fail("Could not record the certificate", str(exc))

    @app.post("/epochs/{epoch_id}/candidates/{snapshot_id}/review")
    async def review(request: Request, epoch_id: str, snapshot_id: str):
        try:
            form = await request.form()
            verify(request, str(form.get("csrf_token", "")), str(form.get("confirmed", "")))
            finding_values = {name: form.get(name) == "yes" for name in FINDING_LABELS}
            job = console.record_review(
                epoch_id, snapshot_id, str(form.get("reviewer_id", "")),
                str(form.get("certificate_digest", "")), str(form.get("decision", "")),
                finding_values, str(form.get("limitations", "")), str(form.get("key_id", "")),
                str(form.get("signing_key_path", "")), str(form.get("public_key_path", "")),
                str(form.get("prior_review_digest", "")),
            )
            return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)
        except Exception as exc:
            return fail("Could not record the decision", str(exc))

    @app.get("/epochs/{epoch_id}/results", response_class=HTMLResponse)
    def results(epoch_id: str):
        try:
            value = console.results(epoch_id)
        except Exception as exc:
            return fail("Could not read results", str(exc), 404)
        public = value["public"]
        if public is None:
            body = f'<div class="breadcrumb"><a href="{_epoch_link(epoch_id)}">{_e(epoch_id)}</a> / Results</div><div class="page-head"><div><div class="eyebrow">Rankings</div><h1>Results are not final</h1><p class="subtitle">Freeze the audit population and close the public epoch after candidate review. BBA will then publish the matrix and both rankings.</p></div></div>'
            return _layout("Results", body)
        def creator_table(rows: list[dict[str, Any]]) -> str:
            return "".join(
            f'<tr><td class="num">{_e(row["rank"] or "—")}</td><td>{_e(row["creator"])}</td><td>{_chip(row["status"])}</td><td class="num">{_percent(row["best_solver_median"])}</td><td class="num">{_percent(row["panel_median"])}</td></tr>'
                for row in rows
            )
        creator_rows = creator_table(public["creator_rankings"]["final_round"])
        blind_rows = creator_table(public["creator_rankings"]["blind_round"])
        solver_rows = "".join(
            f'<tr><td class="num">{_e(row["rank"] or "—")}</td><td>{_e(row["solver"])}</td><td class="num">{_percent(row["macro_accuracy"])}</td><td class="num">{_e(row["canonical_benchmarks"])}</td><td>{_e(" – ".join(_percent(x) for x in row["ci95"]) if row["ci95"] else "—")}</td></tr>'
            for row in public["solver_ranking"]
        )
        solver_ids = sorted({solver for row in public["matrix"].values() for solver in row})
        matrix_head = "".join(f'<th title="{_e(solver)}">{_e(_short(solver, 16))}</th>' for solver in solver_ids)
        matrix_rows = "".join(
            f'<tr><td class="mono" title="{_e(snapshot)}">{_e(_short(snapshot, 28))}</td>' + "".join(
                f'<td class="num">{_percent(cells.get(solver, {}).get("median_accuracy")) if cells.get(solver, {}).get("complete") else _chip((cells.get(solver, {}).get("states") or ["not_run"])[0])}</td>'
                for solver in solver_ids
            ) + '</tr>'
            for snapshot, cells in public["matrix"].items()
        )
        audit = value["audit"]
        if audit is None:
            audit_html = '<div class="notice warn">The sealed audit is not complete.</div>'
        else:
            hidden = audit["targets"]["hidden_only"]
            audit_html = f'<div class="card"><div class="section-head"><div><div class="metric-label">Evaluator audit</div><div class="metric">{_chip(audit["status"])}</div></div></div><dl class="definition"><dt>Spearman agreement</dt><dd>{_percent(hidden["spearman"])}</dd><dt>Pairwise accuracy</dt><dd>{_percent(hidden["pairwise"]["accuracy"])}</dd><dt>Utility recovery</dt><dd>{_percent(hidden["selection_at_quartile"]["utility_recovery"])}</dd><dt>Defect sensitivity</dt><dd>{_percent(hidden["defect_sensitivity"]["accuracy"])}</dd></dl><p class="subtitle">The audit tests transfer to sealed evidence. It does not change the frozen public ranks.</p></div>'
        body = f"""<div class="breadcrumb"><a href="{_epoch_link(epoch_id)}">{_e(epoch_id)}</a> / Results</div><div class="page-head"><div><div class="eyebrow">Closed public evaluation</div><h1>Benchmark rankings</h1><p class="subtitle">Creator rank rewards difficult, valid, approved benchmarks. Solver rank uses equal weight across active canonical benchmarks.</p></div></div>
<section class="grid"><div class="span-12">{audit_html}</div></section><section class="section"><div class="section-head"><h2>Final creator ranking</h2></div><div class="table-wrap"><table><thead><tr><th class="num">Rank</th><th>Creator</th><th>Status</th><th class="num">Best solver</th><th class="num">Panel median</th></tr></thead><tbody>{creator_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>Solver ranking</h2></div><div class="table-wrap"><table><thead><tr><th class="num">Rank</th><th>Solver</th><th class="num">Macro accuracy</th><th class="num">Benchmarks</th><th>95% interval</th></tr></thead><tbody>{solver_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>Blind creator ranking</h2><span class="muted">Round 0, before public feedback</span></div><div class="table-wrap"><table><thead><tr><th class="num">Rank</th><th>Creator</th><th>Status</th><th class="num">Best solver</th><th class="num">Panel median</th></tr></thead><tbody>{blind_rows}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>Creator-by-solver matrix</h2></div><div class="table-wrap"><table class="matrix"><thead><tr><th>Candidate</th>{matrix_head}</tr></thead><tbody>{matrix_rows}</tbody></table></div></section>"""
        return _layout("Rankings", body)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs() -> str:
        rows = "".join(
            f'<tr><td><a href="/jobs/{_u(job["job_id"])}">{_e(job["label"])}</a></td><td>{_chip(job["status"])}</td><td>{_e(job["epoch_id"] or "—")}</td><td>{_e(job["created_at"])}</td></tr>'
            for job in console.jobs.recent()
        ) or '<tr><td class="empty" colspan="4">No console operation has run in this process.</td></tr>'
        body = f'<div class="page-head"><div><div class="eyebrow">Local job queue</div><h1>Operations</h1><p class="subtitle">The console runs one mutation at a time. Epoch evidence remains resumable if this process stops.</p></div></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Epoch</th><th>Created</th></tr></thead><tbody>{rows}</tbody></table></div>'
        return _layout("Operations", body, current="jobs")

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(job_id: str):
        job = console.jobs.get(job_id)
        if job is None:
            return fail("Operation not found", "This operation does not exist in the current console process.", 404)
        running = job["status"] in {"queued", "running"}
        destination = _epoch_link(job["epoch_id"]) if job["epoch_id"] else "/"
        output = job["error"] or job["output"] or "Waiting for the controller."
        body = f'<div class="breadcrumb"><a href="/jobs">Operations</a> / {_e(job_id)}</div><div class="page-head"><div><div class="eyebrow">{_e(job["label"])}</div><h1>{_e(job["status"].capitalize())}</h1><p class="subtitle">Created {_e(job["created_at"])}. This page refreshes while the operation runs.</p></div>{_chip(job["status"])}</div><pre class="job-output">{_e(output)}</pre><div class="button-row section"><a class="button secondary" href="{destination}">Open epoch</a><a class="button secondary" href="/jobs">All operations</a></div>'
        return _layout(job["label"], body, current="jobs", refresh=running)

    return app


def run_console(evidence_root: Path, port: int = 8765) -> None:
    """Run the operator console on the IPv4 loopback interface."""

    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    console = OperatorConsole(evidence_root)
    uvicorn.run(create_app(console), host="127.0.0.1", port=port, log_level="info")
