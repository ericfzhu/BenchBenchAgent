"""Phase-aware localhost development portal for BenchBenchAgent."""

from __future__ import annotations

import html
import secrets
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import uvicorn
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bba import _web as legacy
from bba.epoch_setup import new_epoch_id
from bba.operator import OperatorConsole


CSS = """
:root {
  color-scheme: light;
  --canvas: #f3f5f8;
  --panel: #ffffff;
  --panel-soft: #f8fafc;
  --ink: #15202b;
  --muted: #617184;
  --line: #dce3ea;
  --brand: #3657d6;
  --brand-dark: #263fa4;
  --brand-soft: #eef1ff;
  --success: #14825b;
  --success-soft: #e9f7f1;
  --warning: #a9640b;
  --warning-soft: #fff4df;
  --danger: #bd3b47;
  --danger-soft: #fdecef;
  --nav: #101722;
  --radius: 16px;
  --shadow: 0 1px 2px rgba(15,23,42,.04), 0 14px 34px rgba(15,23,42,.07);
}
* { box-sizing: border-box; }
html { font-size: 16px; -webkit-font-smoothing: antialiased; }
body { margin: 0; background: var(--canvas); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: var(--brand); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3 { color: var(--ink); letter-spacing: -.035em; margin: 0; }
h1 { font-size: clamp(30px, 4vw, 46px); line-height: 1.05; }
h2 { font-size: 20px; }
h3 { font-size: 15px; }
p { line-height: 1.65; }
.shell { min-height: 100vh; display: grid; grid-template-columns: 252px minmax(0,1fr); }
.side { position: sticky; top: 0; height: 100vh; background: var(--nav); color: #fff; padding: 26px 20px; display: flex; flex-direction: column; gap: 28px; }
.brand { color: #fff; display: block; font-size: 21px; font-weight: 800; letter-spacing: -.04em; }
.brand:hover { text-decoration: none; }
.brand small { color: #8fa0b6; display: block; font-size: 10px; font-weight: 800; letter-spacing: .16em; margin-top: 7px; text-transform: uppercase; }
.side nav { display: grid; gap: 6px; }
.side nav a { align-items: center; border-radius: 10px; color: #b9c4d1; display: flex; font-size: 14px; font-weight: 650; min-height: 44px; padding: 0 12px; }
.side nav a:hover, .side nav a.current { background: rgba(255,255,255,.085); color: #fff; text-decoration: none; }
.local-note { border-top: 1px solid rgba(255,255,255,.1); color: #8fa0b6; font-size: 12px; line-height: 1.6; margin-top: auto; padding-top: 18px; }
.local-dot { background: #47d89b; border-radius: 50%; box-shadow: 0 0 0 4px rgba(71,216,155,.13); display: inline-block; height: 8px; margin-right: 9px; width: 8px; }
.main { min-width: 0; padding: 42px clamp(22px,5vw,72px) 80px; }
.page-head { align-items: flex-start; display: flex; gap: 24px; justify-content: space-between; margin-bottom: 28px; }
.eyebrow { color: var(--brand); font-size: 11px; font-weight: 850; letter-spacing: .14em; margin-bottom: 9px; text-transform: uppercase; }
.subtitle { color: var(--muted); margin: 11px 0 0; max-width: 760px; }
.hero { background: linear-gradient(135deg,#17233d 0%,#263f9c 62%,#4e70e6 100%); border-radius: 22px; box-shadow: var(--shadow); color: #fff; overflow: hidden; padding: clamp(26px,5vw,48px); position: relative; }
.hero::after { background: radial-gradient(circle,rgba(255,255,255,.2),transparent 66%); content: ""; height: 340px; position: absolute; right: -90px; top: -170px; width: 340px; }
.hero h1, .hero h2 { color: #fff; }
.hero .subtitle { color: #d8e0f4; max-width: 690px; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 24px; position: relative; z-index: 1; }
.grid { display: grid; gap: 18px; grid-template-columns: repeat(12,minmax(0,1fr)); }
.span-3 { grid-column: span 3; } .span-4 { grid-column: span 4; } .span-5 { grid-column: span 5; } .span-6 { grid-column: span 6; } .span-7 { grid-column: span 7; } .span-8 { grid-column: span 8; } .span-9 { grid-column: span 9; } .span-12 { grid-column: span 12; }
.card { background: var(--panel); border: 1px solid rgba(220,227,234,.85); border-radius: var(--radius); box-shadow: var(--shadow); padding: 22px; }
.card.soft { background: var(--panel-soft); box-shadow: none; }
.card.current { border-color: rgba(54,87,214,.45); box-shadow: 0 0 0 3px rgba(54,87,214,.08), var(--shadow); }
.section { margin-top: 34px; }
.section-head { align-items: center; display: flex; gap: 16px; justify-content: space-between; margin-bottom: 15px; }
.metric-label { color: var(--muted); font-size: 11px; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
.metric { font-size: 30px; font-variant-numeric: tabular-nums; font-weight: 780; letter-spacing: -.04em; margin-top: 8px; }
.mini { color: var(--muted); font-size: 12px; margin-top: 6px; }
.readiness { display: grid; gap: 12px; }
.check-card { align-items: flex-start; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 13px; display: flex; gap: 12px; padding: 14px; }
.check-icon { align-items: center; border-radius: 50%; display: inline-flex; flex: 0 0 30px; font-size: 14px; font-weight: 850; height: 30px; justify-content: center; }
.check-icon.passed { background: var(--success-soft); color: var(--success); }
.check-icon.warning { background: var(--warning-soft); color: var(--warning); }
.check-icon.failed { background: var(--danger-soft); color: var(--danger); }
.check-copy strong { display: block; font-size: 13px; }
.check-copy span { color: var(--muted); display: block; font-size: 12px; line-height: 1.5; margin-top: 3px; overflow-wrap: anywhere; }
.workflow { display: grid; gap: 9px; grid-template-columns: repeat(7,minmax(0,1fr)); }
.workflow-step { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; min-height: 90px; padding: 13px; position: relative; }
.workflow-step.complete { background: var(--success-soft); border-color: #c9ebdc; }
.workflow-step.current { background: var(--brand-soft); border-color: #bfc9fb; }
.workflow-step .index { color: var(--muted); font-size: 10px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }
.workflow-step strong { display: block; font-size: 12px; line-height: 1.35; margin-top: 8px; }
.progress { background: #e7ebf0; border-radius: 999px; height: 8px; overflow: hidden; }
.progress > span { background: var(--brand); display: block; height: 100%; }
.epoch-card { display: grid; gap: 16px; }
.epoch-card-top { align-items: flex-start; display: flex; gap: 12px; justify-content: space-between; }
.epoch-card h3 { font-size: 17px; overflow-wrap: anywhere; }
.action-card { display: flex; flex-direction: column; min-height: 210px; }
.action-card p { color: var(--muted); font-size: 13px; }
.action-card .button { margin-top: auto; }
.table-wrap { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); overflow: auto; }
table { border-collapse: collapse; min-width: 720px; width: 100%; }
th, td { border-bottom: 1px solid var(--line); padding: 14px 16px; text-align: left; vertical-align: middle; }
th { color: var(--muted); font-size: 10px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover { background: #f8faff; }
.num { font-variant-numeric: tabular-nums; text-align: right; }
.mono { font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size: .88em; }
.muted { color: var(--muted); }
.chip { align-items: center; border-radius: 999px; display: inline-flex; font-size: 10px; font-weight: 820; letter-spacing: .02em; min-height: 25px; padding: 4px 9px; white-space: nowrap; }
.chip.green { background: var(--success-soft); color: var(--success); }
.chip.blue { background: var(--brand-soft); color: var(--brand-dark); }
.chip.amber { background: var(--warning-soft); color: var(--warning); }
.chip.red { background: var(--danger-soft); color: var(--danger); }
.chip.gray { background: #edf0f4; color: #586577; }
.button { align-items: center; background: var(--brand); border: 0; border-radius: 10px; color: #fff; cursor: pointer; display: inline-flex; font: inherit; font-size: 13px; font-weight: 750; justify-content: center; min-height: 44px; padding: 0 16px; }
.button:hover { background: var(--brand-dark); color: #fff; text-decoration: none; }
.button.secondary { background: #fff; border: 1px solid var(--line); color: var(--ink); }
.button.secondary:hover { background: #f8fafc; color: var(--ink); }
.button.ghost { background: rgba(255,255,255,.12); border: 1px solid rgba(255,255,255,.2); }
.button.danger { background: var(--danger); }
.button[disabled] { cursor: not-allowed; opacity: .42; }
.button-row { display: flex; flex-wrap: wrap; gap: 10px; }
form.stack { display: grid; gap: 15px; }
.field { display: grid; gap: 7px; }
.field > label, fieldset > legend { font-size: 12px; font-weight: 750; }
input, select, textarea { background: #fff; border: 1px solid #cfd7e1; border-radius: 9px; color: var(--ink); font: inherit; font-size: 14px; min-height: 44px; outline: none; padding: 10px 12px; width: 100%; }
input:focus, select:focus, textarea:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(54,87,214,.13); }
textarea { min-height: 100px; resize: vertical; }
.check { align-items: flex-start; display: flex; gap: 10px; min-height: 38px; padding: 7px 0; }
.check input { flex: 0 0 20px; min-height: 20px; width: 20px; }
fieldset { border: 0; margin: 0; padding: 0; }
.hint { color: var(--muted); font-size: 12px; line-height: 1.55; margin: 0; }
.notice { background: var(--brand-soft); border-left: 4px solid var(--brand); border-radius: 0 10px 10px 0; padding: 14px 16px; }
.notice.warn { background: var(--warning-soft); border-color: var(--warning); }
.notice.error { background: var(--danger-soft); border-color: var(--danger); }
.notice.success { background: var(--success-soft); border-color: var(--success); }
.empty { color: var(--muted); padding: 38px 22px; text-align: center; }
.job-output { background: #0e1621; border-radius: 12px; color: #dce6f1; max-height: 520px; overflow: auto; padding: 18px; white-space: pre-wrap; }
.matrix td, .matrix th { font-size: 12px; min-width: 105px; }
.matrix th:first-child, .matrix td:first-child { background: var(--panel); left: 0; min-width: 220px; position: sticky; z-index: 1; }
.breadcrumb { color: var(--muted); font-size: 12px; margin-bottom: 17px; }
.definition { display: grid; grid-template-columns: minmax(130px,.42fr) 1fr; margin: 0; }
.definition dt, .definition dd { border-bottom: 1px solid var(--line); margin: 0; padding: 11px 0; }
.definition dt { color: var(--muted); font-size: 12px; }
.definition dd { font-size: 13px; overflow-wrap: anywhere; }
.badge-line { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
@media (max-width: 1100px) { .workflow { grid-template-columns: repeat(4,1fr); } .span-3 { grid-column: span 6; } }
@media (max-width: 820px) {
  .shell { display: block; }
  .side { height: auto; padding: 17px 18px; position: static; }
  .side nav { display: flex; overflow-x: auto; }
  .local-note { display: none; }
  .main { padding: 26px 16px 55px; }
  .page-head { display: block; }
  .page-head > :last-child { margin-top: 14px; }
  .span-3,.span-4,.span-5,.span-6,.span-7,.span-8,.span-9 { grid-column: span 12; }
  .workflow { grid-template-columns: repeat(2,1fr); }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } }
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _u(value: Any) -> str:
    return quote(str(value), safe="")


def _tone(status: str) -> str:
    if status in {"passed", "active", "approved", "audited", "validated", "succeeded", "public_closed"}:
        return "green"
    if status in {"running", "public_running", "awaiting_review", "queued", "audit_population_frozen", "current"}:
        return "blue"
    if status in {"failed", "invalid", "rejected", "unvalidated", "unreadable"}:
        return "red"
    if status in {"warning", "solvability_audit", "escalated", "too_easy"}:
        return "amber"
    return "gray"


def _chip(status: str) -> str:
    return f'<span class="chip {_tone(status)}">{_e(status.replace("_", " "))}</span>'


def _layout(title: str, body: str, *, current: str = "epochs", refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}<title>{_e(title)} · BBA</title><style>{CSS}</style></head><body><div class="shell"><aside class="side"><a class="brand" href="/">BenchBenchAgent<small>Local development portal</small></a><nav><a class="{'current' if current == 'epochs' else ''}" href="/">Workspace</a><a class="{'current' if current == 'jobs' else ''}" href="/jobs">Operations</a></nav><div class="local-note"><span class="local-dot"></span>Controller online<br>Loopback access only</div></aside><main class="main">{body}</main></div></body></html>"""


def _csrf(token: str) -> str:
    return f'<input type="hidden" name="csrf_token" value="{_e(token)}">'


def _epoch_link(epoch_id: str) -> str:
    return f"/epochs/{_u(epoch_id)}"


def _phase_percent(phase: str) -> int:
    phases = {
        "created": 8,
        "public_running": 30,
        "awaiting_review": 58,
        "audit_population_frozen": 72,
        "public_closed": 86,
        "audited": 100,
    }
    return phases.get(phase, 0)


def _drop_get_route(app, path: str) -> None:
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == path
            and "GET" in (getattr(route, "methods", set()) or set())
        )
    ]


def create_app(console: OperatorConsole):
    # Reuse the mature review, result, observability, CSRF, origin, and host
    # routes while replacing the workspace and epoch-control experience.
    legacy.CSS = CSS
    legacy._layout = _layout
    app = legacy.create_app(console)
    for path in ("/", "/epochs/{epoch_id}", "/jobs"):
        _drop_get_route(app, path)

    def fail(title: str, message: str, status: int = 400) -> HTMLResponse:
        body = f'<div class="page-head"><div><div class="eyebrow">Request stopped</div><h1>{_e(title)}</h1><p class="subtitle">{_e(message)}</p></div></div><a class="button secondary" href="/">Return to workspace</a>'
        return HTMLResponse(_layout(title, body), status_code=status)

    def verify_csrf(token: str) -> None:
        if not secrets.compare_digest(token, app.state.csrf_token):
            raise ValueError("the form token is invalid or expired")

    @app.get("/", response_class=HTMLResponse)
    def workspace(request: Request) -> str:
        readiness = console.readiness()
        epochs = console.list_epochs()
        recent = console.jobs.recent(5)
        passed = sum(item["status"] == "passed" for item in readiness["checks"])
        required = sum(item["required"] for item in readiness["checks"])
        check_html = "".join(
            f'<div class="check-card"><span class="check-icon {_e(item["status"])}">{"✓" if item["status"] == "passed" else "!"}</span><div class="check-copy"><strong>{_e(item["label"])}</strong><span>{_e(item["detail"])}</span></div></div>'
            for item in readiness["checks"]
        )
        diagnostic_forms = "".join(
            f'<form method="post" action="/diagnostics/{_u(action)}">{_csrf(request.app.state.csrf_token)}<button class="button secondary" type="submit">{_e(label)}</button></form>'
            for action, label in console.DIAGNOSTIC_ACTIONS.items()
        )
        epoch_cards = "".join(
            f'<article class="card span-4 epoch-card"><div class="epoch-card-top"><div><div class="metric-label">{_e(item.get("catalog_version", "Saved epoch"))}</div><h3><a href="{_epoch_link(item["epoch_id"])}">{_e(item["epoch_id"])}</a></h3></div>{_chip(item.get("phase", "unknown"))}</div><div class="progress"><span style="width:{_phase_percent(item.get("phase", ""))}%"></span></div><div class="badge-line"><span class="muted">{_e(item.get("snapshots", 0))} snapshots</span><span class="muted">·</span><span class="muted">{_e(item.get("solver_cells", 0))} solver cells</span></div><a class="button secondary" href="{_epoch_link(item["epoch_id"])}">Open epoch</a></article>'
            for item in epochs
        ) or '<div class="card span-12 empty">No epoch exists yet. Create one after the required readiness checks pass.</div>'
        job_rows = "".join(
            f'<tr><td><a href="/jobs/{_u(job["job_id"])}">{_e(job["label"])}</a></td><td>{_chip(job["status"])}</td><td>{_e(job["epoch_id"] or "Workspace")}</td></tr>'
            for job in recent
        ) or '<tr><td class="empty" colspan="3">No operation has run in this portal process.</td></tr>'
        body = f"""<section class="hero"><div class="eyebrow" style="color:#aebfff">Local-first benchmark operations</div><h1>Build, test, and run epochs from one workspace.</h1><p class="subtitle">Check Ubuntu and Google Cloud readiness, launch deterministic local diagnostics, and follow each paid epoch through preflight, public evaluation, human review, publication, and sealed audit.</p><div class="hero-actions"><a class="button ghost" href="#new-epoch">Create an epoch</a><a class="button ghost" href="#diagnostics">Run diagnostics</a></div></section>
<section class="section grid"><article class="card span-7"><div class="section-head"><div><div class="eyebrow">Readiness</div><h2>{'Ready to create epochs' if readiness['ready'] else 'Action required before paid work'}</h2></div>{_chip('passed' if readiness['ready'] else 'failed')}</div><div class="readiness">{check_html}</div></article><article class="card span-5" id="new-epoch"><div class="eyebrow">New epoch</div><h2>Freeze a new experiment</h2><p class="subtitle">Uses {_e(readiness['catalog_version'])} with {_e(readiness['model_count'])} serverless model routes.</p><form class="stack section" method="post" action="/epochs">{_csrf(request.app.state.csrf_token)}<div class="field"><label for="epoch-id">Epoch ID</label><input id="epoch-id" name="epoch_id" value="{_e(new_epoch_id())}" pattern="[a-zA-Z0-9._-]+" required></div><button class="button" type="submit" {'disabled' if not readiness['ready'] else ''}>Create immutable epoch</button><p class="hint">Evidence root: <span class="mono">{_e(readiness['evidence_root'])}</span></p></form></article></section>
<section class="section" id="diagnostics"><div class="section-head"><div><div class="eyebrow">Local testing</div><h2>Diagnostics</h2></div><span class="muted">Python {_e(readiness['python'])} · ADK {_e(readiness['google_adk'])}</span></div><div class="card"><div class="button-row">{diagnostic_forms}</div><p class="hint" style="margin-top:12px">Diagnostics use the same serialized operation queue as epoch mutations. The unit suite does not make paid model calls unless a test explicitly invokes live infrastructure.</p></div></section>
<section class="section"><div class="section-head"><h2>Epochs</h2><span class="muted">{len(epochs)} saved in this evidence root</span></div><div class="grid">{epoch_cards}</div></section>
<section class="section"><div class="section-head"><h2>Recent operations</h2><a href="/jobs">View all</a></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Scope</th></tr></thead><tbody>{job_rows}</tbody></table></div></section>"""
        return _layout("Workspace", body)

    @app.post("/diagnostics/{action}")
    def diagnostic(request: Request, action: str, csrf_token: str = Form(...)):
        try:
            verify_csrf(csrf_token)
            job = console.run_diagnostic(action)
            return RedirectResponse(f"/jobs/{job.job_id}", status_code=303)
        except Exception as exc:
            return fail("Could not start the diagnostic", str(exc))

    @app.get("/epochs/{epoch_id}", response_class=HTMLResponse)
    def epoch_control(request: Request, epoch_id: str):
        try:
            value = console.epoch(epoch_id)
        except Exception as exc:
            return fail("Could not read the epoch", str(exc), 404)
        jobs = [item for item in console.jobs.recent() if item["epoch_id"] == epoch_id]
        active_job = next(
            (item for item in jobs if item["status"] in {"queued", "running"}),
            None,
        )
        workflow = "".join(
            f'<div class="workflow-step {"complete" if step["complete"] else "current" if step["current"] else ""}"><div class="index">Step {index}</div><strong>{_e(step["label"])}</strong>{"<div class=mini>Complete</div>" if step["complete"] else "<div class=mini>Next</div>" if step["current"] else ""}</div>'
            for index, step in enumerate(value["workflow"], 1)
        )
        action_cards = []
        for action, label in console.EPOCH_ACTIONS.items():
            state = value["action_states"][action]
            disabled = bool(active_job) or not state["enabled"]
            action_cards.append(
                f'<form class="card span-4 action-card {"current" if state["enabled"] and not state["complete"] else ""}" method="post" action="{_epoch_link(epoch_id)}/actions/{_u(action)}">{_csrf(request.app.state.csrf_token)}<div class="badge-line">{_chip("passed" if state["complete"] else "current" if state["enabled"] else "locked")}</div><h3 style="margin-top:14px">{_e(label)}</h3><p>{_e(state["hint"])}</p><label class="check"><input type="checkbox" name="confirmed" value="yes" required {"disabled" if disabled else ""}><span>Confirm this operation</span></label><button class="button {"danger" if action == "audit" else "secondary"}" type="submit" {"disabled" if disabled else ""}>{_e(label)}</button></form>'
            )
        candidates = value["candidates"]
        candidate_rows = "".join(
            f'<tr><td><a href="{_epoch_link(epoch_id)}/candidates/{_u(item["snapshot_id"])}">{_e(item["model"])}</a><br><span class="mono muted">{_e(item["snapshot_id"][:32])}…</span></td><td class="num">{item["round"]}</td><td>{_chip(item["status"])}</td><td class="num">{item["solver_cells"]}</td><td class="num">{"—" if item["best_solver_median"] is None else f"{item['best_solver_median']*100:.1f}%"}</td><td class="num">{item["certificate_count"]}</td></tr>'
            for item in candidates
        ) or '<tr><td class="empty" colspan="6">No candidate has been frozen yet.</td></tr>'
        failed = value.get("failed_work", [])
        failed_html = "" if not failed else '<section class="notice error section"><strong>Failed work needs attention.</strong><ul>' + "".join(f'<li><span class="mono">{_e(item["work_id"])}</span>: {_e(item.get("error", "unknown error"))}</li>' for item in failed) + "</ul></section>"
        manifest = value["manifest"]
        usage = value["usage"]
        active_html = "" if not active_job else f'<div class="notice"><strong>{_e(active_job["label"])} is {_e(active_job["status"])}.</strong> <a href="/jobs/{_u(active_job["job_id"])}">Open live output</a>.</div>'
        review_notice = '<div class="notice success"><strong>Review window is open.</strong> Open final-round candidates to add independent certificates and signed decisions.</div>' if value["review_open"] and value["phase"] == "awaiting_review" else '<div class="notice warn"><strong>Review inputs are frozen.</strong> Certificates and decisions are now read-only.</div>' if not value["review_open"] else ""
        body = f"""<div class="breadcrumb"><a href="/">Workspace</a> / {_e(epoch_id)}</div><div class="page-head"><div><div class="eyebrow">{_e(manifest['catalog_version'])}</div><h1>{_e(epoch_id)}</h1><p class="subtitle">{manifest['models']} models · {manifest['rounds']} creator rounds · {manifest['solver_repetitions']} solver repetitions · {_e(manifest['gcp_location'])} Vertex routing</p></div>{_chip(value['phase'])}</div>{active_html}{review_notice}
<section class="section"><div class="section-head"><h2>Workflow</h2><span class="muted">Phase-aware controls prevent out-of-order operations</span></div><div class="workflow">{workflow}</div></section>
<section class="section grid"><article class="card span-3"><div class="metric-label">Snapshots</div><div class="metric">{value['snapshots']}</div></article><article class="card span-3"><div class="metric-label">Solver cells</div><div class="metric">{value['solver_cells']}</div></article><article class="card span-3"><div class="metric-label">Model calls reserved/used</div><div class="metric">{usage['calls']}</div></article><article class="card span-3"><div class="metric-label">Conservative cost</div><div class="metric">${usage['estimated_cost_usd']:.2f}</div><div class="mini">Limit ${value.get('max_estimated_cost_usd') or 0:.2f}</div></article></section>{failed_html}
<section class="section"><div class="section-head"><h2>Epoch controls</h2><div class="button-row"><a class="button secondary" href="{_epoch_link(epoch_id)}/observability">Agent activity</a><a class="button secondary" href="{_epoch_link(epoch_id)}/results">Rankings</a></div></div><div class="grid">{"".join(action_cards)}</div></section>
<section class="section"><div class="section-head"><h2>Candidate benchmarks</h2><span class="muted">{len(candidates)} snapshots</span></div><div class="table-wrap"><table><thead><tr><th>Candidate</th><th class="num">Round</th><th>Status</th><th class="num">Cells</th><th class="num">Best solver</th><th class="num">Certificates</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>"""
        return _layout(epoch_id, body)

    @app.get("/jobs", response_class=HTMLResponse)
    def operations() -> str:
        jobs = console.jobs.recent(50)
        rows = "".join(
            f'<tr><td><a href="/jobs/{_u(job["job_id"])}">{_e(job["label"])}</a></td><td>{_chip(job["status"])}</td><td>{_e(job["epoch_id"] or "Workspace")}</td><td>{_e(job["created_at"])}</td><td>{_e(job["finished_at"] or "—")}</td></tr>'
            for job in jobs
        ) or '<tr><td class="empty" colspan="5">No operation has run in this portal process.</td></tr>'
        body = f'<div class="page-head"><div><div class="eyebrow">Serialized local execution</div><h1>Operations</h1><p class="subtitle">Diagnostics and epoch mutations share one queue. Open an operation to watch output while it runs.</p></div></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Scope</th><th>Started</th><th>Finished</th></tr></thead><tbody>{rows}</tbody></table></div>'
        return _layout("Operations", body, current="jobs")

    return app


def run_console(evidence_root: Path, port: int = 8765) -> None:
    """Run the development portal on the IPv4 loopback interface."""

    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    console = OperatorConsole(evidence_root)
    uvicorn.run(
        create_app(console),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
