"""Implementation of the phase-aware localhost development portal."""

from __future__ import annotations

import html
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bba import _web as legacy
from bba.epoch_setup import new_epoch_id
from bba.operator import OperatorConsole


CSS = """
:root{color-scheme:light;--bg:#f3f5f8;--panel:#fff;--soft:#f8fafc;--ink:#15202b;--muted:#617184;--line:#dce3ea;--brand:#3657d6;--brand2:#263fa4;--ok:#14825b;--okbg:#e9f7f1;--warn:#a9640b;--warnbg:#fff4df;--bad:#bd3b47;--badbg:#fdecef;--nav:#101722;--r:16px;--shadow:0 1px 2px rgba(15,23,42,.04),0 14px 34px rgba(15,23,42,.07)}
*{box-sizing:border-box}html{-webkit-font-smoothing:antialiased}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}h1,h2,h3{letter-spacing:-.035em;margin:0}h1{font-size:clamp(30px,4vw,46px);line-height:1.05}h2{font-size:20px}h3{font-size:15px}p{line-height:1.65}.shell{min-height:100vh;display:grid;grid-template-columns:252px minmax(0,1fr)}.side{position:sticky;top:0;height:100vh;background:var(--nav);color:#fff;padding:26px 20px;display:flex;flex-direction:column;gap:28px}.brand{color:#fff;font-size:21px;font-weight:800;letter-spacing:-.04em}.brand:hover{text-decoration:none}.brand small{color:#8fa0b6;display:block;font-size:10px;font-weight:800;letter-spacing:.16em;margin-top:7px;text-transform:uppercase}.side nav{display:grid;gap:6px}.side nav a{display:flex;align-items:center;min-height:44px;padding:0 12px;border-radius:10px;color:#b9c4d1;font-size:14px;font-weight:650}.side nav a:hover,.side nav a.current{background:rgba(255,255,255,.085);color:#fff;text-decoration:none}.local-note{margin-top:auto;padding-top:18px;border-top:1px solid rgba(255,255,255,.1);color:#8fa0b6;font-size:12px;line-height:1.6}.local-dot{display:inline-block;width:8px;height:8px;margin-right:9px;border-radius:50%;background:#47d89b;box-shadow:0 0 0 4px rgba(71,216,155,.13)}.main{min-width:0;padding:42px clamp(22px,5vw,72px) 80px}.page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:28px}.eyebrow{color:var(--brand);font-size:11px;font-weight:850;letter-spacing:.14em;margin-bottom:9px;text-transform:uppercase}.subtitle{color:var(--muted);margin:11px 0 0;max-width:760px}.hero{position:relative;overflow:hidden;padding:clamp(26px,5vw,48px);border-radius:22px;background:linear-gradient(135deg,#17233d,#263f9c 62%,#4e70e6);box-shadow:var(--shadow);color:#fff}.hero h1{color:#fff}.hero .subtitle{color:#d8e0f4}.hero-actions,.button-row,.badge-line{display:flex;flex-wrap:wrap;gap:10px}.hero-actions{margin-top:24px}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:18px}.span-3{grid-column:span 3}.span-4{grid-column:span 4}.span-5{grid-column:span 5}.span-6{grid-column:span 6}.span-7{grid-column:span 7}.span-8{grid-column:span 8}.span-9{grid-column:span 9}.span-12{grid-column:span 12}.card{padding:22px;border:1px solid rgba(220,227,234,.9);border-radius:var(--r);background:var(--panel);box-shadow:var(--shadow)}.card.current{border-color:rgba(54,87,214,.45);box-shadow:0 0 0 3px rgba(54,87,214,.08),var(--shadow)}.section{margin-top:34px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:15px}.metric-label{color:var(--muted);font-size:11px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.metric{margin-top:8px;font-size:30px;font-weight:780;letter-spacing:-.04em;font-variant-numeric:tabular-nums}.mini,.hint,.muted{color:var(--muted)}.mini,.hint{font-size:12px}.readiness{display:grid;gap:12px}.check-card{display:flex;align-items:flex-start;gap:12px;padding:14px;border:1px solid var(--line);border-radius:13px;background:var(--soft)}.check-icon{display:inline-flex;align-items:center;justify-content:center;flex:0 0 30px;width:30px;height:30px;border-radius:50%;font-weight:850}.check-icon.passed{background:var(--okbg);color:var(--ok)}.check-icon.warning{background:var(--warnbg);color:var(--warn)}.check-icon.failed{background:var(--badbg);color:var(--bad)}.check-copy strong,.check-copy span{display:block}.check-copy strong{font-size:13px}.check-copy span{margin-top:3px;color:var(--muted);font-size:12px;line-height:1.5;overflow-wrap:anywhere}.workflow{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:9px}.workflow-step{min-height:88px;padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.workflow-step.complete{border-color:#c9ebdc;background:var(--okbg)}.workflow-step.current{border-color:#bfc9fb;background:#eef1ff}.workflow-step .index{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.09em;text-transform:uppercase}.workflow-step strong{display:block;margin-top:8px;font-size:12px;line-height:1.35}.progress{height:8px;overflow:hidden;border-radius:999px;background:#e7ebf0}.progress span{display:block;height:100%;background:var(--brand)}.epoch-card,.action-card{display:flex;flex-direction:column;gap:15px}.epoch-card-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.epoch-card h3{font-size:17px;overflow-wrap:anywhere}.action-card{min-height:210px}.action-card p{color:var(--muted);font-size:13px}.action-card .button{margin-top:auto}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--r);background:var(--panel);box-shadow:var(--shadow)}table{width:100%;min-width:720px;border-collapse:collapse}th,td{padding:14px 16px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}th{color:var(--muted);font-size:10px;font-weight:850;letter-spacing:.09em;text-transform:uppercase}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#f8faff}.num{text-align:right;font-variant-numeric:tabular-nums}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}.chip{display:inline-flex;align-items:center;min-height:25px;padding:4px 9px;border-radius:999px;font-size:10px;font-weight:820;white-space:nowrap}.chip.green{background:var(--okbg);color:var(--ok)}.chip.blue{background:#eef1ff;color:var(--brand2)}.chip.amber{background:var(--warnbg);color:var(--warn)}.chip.red{background:var(--badbg);color:var(--bad)}.chip.gray{background:#edf0f4;color:#586577}.button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 16px;border:0;border-radius:10px;background:var(--brand);color:#fff;cursor:pointer;font:inherit;font-size:13px;font-weight:750}.button:hover{background:var(--brand2);color:#fff;text-decoration:none}.button.secondary{border:1px solid var(--line);background:#fff;color:var(--ink)}.button.ghost{border:1px solid rgba(255,255,255,.2);background:rgba(255,255,255,.12)}.button.danger{background:var(--bad)}.button[disabled]{cursor:not-allowed;opacity:.42}form.stack{display:grid;gap:15px}.field{display:grid;gap:7px}.field>label,fieldset>legend{font-size:12px;font-weight:750}input,select,textarea{width:100%;min-height:44px;padding:10px 12px;border:1px solid #cfd7e1;border-radius:9px;background:#fff;color:var(--ink);outline:none;font:inherit;font-size:14px}input:focus,select:focus,textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(54,87,214,.13)}textarea{min-height:100px;resize:vertical}.check{display:flex;align-items:flex-start;gap:10px;min-height:38px;padding:7px 0}.check input{flex:0 0 20px;width:20px;min-height:20px}fieldset{border:0;margin:0;padding:0}.notice{padding:14px 16px;border-left:4px solid var(--brand);border-radius:0 10px 10px 0;background:#eef1ff}.notice.warn{border-color:var(--warn);background:var(--warnbg)}.notice.error{border-color:var(--bad);background:var(--badbg)}.notice.success{border-color:var(--ok);background:var(--okbg)}.empty{padding:38px 22px;color:var(--muted);text-align:center}.job-output{max-height:520px;overflow:auto;padding:18px;border-radius:12px;background:#0e1621;color:#dce6f1;white-space:pre-wrap}.matrix td,.matrix th{min-width:105px;font-size:12px}.matrix th:first-child,.matrix td:first-child{position:sticky;left:0;z-index:1;min-width:220px;background:var(--panel)}.breadcrumb{margin-bottom:17px;color:var(--muted);font-size:12px}.definition{display:grid;grid-template-columns:minmax(130px,.42fr) 1fr;margin:0}.definition dt,.definition dd{margin:0;padding:11px 0;border-bottom:1px solid var(--line)}.definition dt{color:var(--muted);font-size:12px}.definition dd{font-size:13px;overflow-wrap:anywhere}@media(max-width:1100px){.workflow{grid-template-columns:repeat(4,1fr)}.span-3{grid-column:span 6}}@media(max-width:820px){.shell{display:block}.side{position:static;height:auto;padding:17px 18px}.side nav{display:flex;overflow-x:auto}.local-note{display:none}.main{padding:26px 16px 55px}.page-head{display:block}.span-3,.span-4,.span-5,.span-6,.span-7,.span-8,.span-9{grid-column:span 12}.workflow{grid-template-columns:repeat(2,1fr)}}
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
    return '<span class="chip %s">%s</span>' % (
        _tone(status),
        _e(status.replace("_", " ")),
    )


def _percent(value: Any) -> str:
    return "—" if value is None else "%.1f%%" % (float(value) * 100)


def _layout(title: str, body: str, *, current: str = "epochs", refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}<title>{_e(title)} · BBA</title><style>{CSS}</style></head><body><div class="shell"><aside class="side"><a class="brand" href="/">BenchBenchAgent<small>Local development portal</small></a><nav><a class="{'current' if current == 'epochs' else ''}" href="/">Workspace</a><a class="{'current' if current == 'jobs' else ''}" href="/jobs">Operations</a></nav><div class="local-note"><span class="local-dot"></span>Controller online<br>Loopback access only</div></aside><main class="main">{body}</main></div></body></html>"""


def _csrf(token: str) -> str:
    return '<input type="hidden" name="csrf_token" value="%s">' % _e(token)


def _epoch_link(epoch_id: str) -> str:
    return "/epochs/%s" % _u(epoch_id)


def _phase_percent(phase: str) -> int:
    return {"created": 8, "public_running": 30, "awaiting_review": 58, "audit_population_frozen": 72, "public_closed": 86, "audited": 100}.get(phase, 0)


def _drop_get_route(app, path: str) -> None:
    app.router.routes = [route for route in app.router.routes if not (getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", set()) or set()))]


def create_app(console: OperatorConsole):
    legacy.CSS = CSS
    legacy._layout = _layout
    app = legacy.create_app(console)
    for path in ("/", "/epochs/{epoch_id}", "/jobs"):
        _drop_get_route(app, path)

    def fail(title: str, message: str, status: int = 400) -> HTMLResponse:
        body = '<div class="page-head"><div><div class="eyebrow">Request stopped</div><h1>%s</h1><p class="subtitle">%s</p></div></div><a class="button secondary" href="/">Return to workspace</a>' % (_e(title), _e(message))
        return HTMLResponse(_layout(title, body), status_code=status)

    def verify_csrf(token: str) -> None:
        if not secrets.compare_digest(token, app.state.csrf_token):
            raise ValueError("the form token is invalid or expired")

    @app.get("/", response_class=HTMLResponse)
    def workspace(request: Request) -> str:
        readiness = console.readiness()
        epochs = console.list_epochs()
        recent = console.jobs.recent(5)
        check_html = "".join(
            '<div class="check-card"><span class="check-icon %s">%s</span><div class="check-copy"><strong>%s</strong><span>%s</span></div></div>' % (
                _e(item["status"]),
                "✓" if item["status"] == "passed" else "!",
                _e(item["label"]),
                _e(item["detail"]),
            )
            for item in readiness["checks"]
        )
        diagnostics = "".join(
            '<form method="post" action="/diagnostics/%s">%s<button class="button secondary" type="submit">%s</button></form>' % (_u(action), _csrf(request.app.state.csrf_token), _e(label))
            for action, label in console.DIAGNOSTIC_ACTIONS.items()
        )
        epoch_cards = "".join(
            '<article class="card span-4 epoch-card"><div class="epoch-card-top"><div><div class="metric-label">%s</div><h3><a href="%s">%s</a></h3></div>%s</div><div class="progress"><span style="width:%d%%"></span></div><div class="badge-line"><span class="muted">%s snapshots</span><span class="muted">·</span><span class="muted">%s solver cells</span></div><a class="button secondary" href="%s">Open epoch</a></article>' % (
                _e(item.get("catalog_version", "Saved epoch")), _epoch_link(item["epoch_id"]), _e(item["epoch_id"]), _chip(item.get("phase", "unknown")), _phase_percent(item.get("phase", "")), _e(item.get("snapshots", 0)), _e(item.get("solver_cells", 0)), _epoch_link(item["epoch_id"]),
            )
            for item in epochs
        ) or '<div class="card span-12 empty">No epoch exists yet. Create one after the required readiness checks pass.</div>'
        jobs = "".join(
            '<tr><td><a href="/jobs/%s">%s</a></td><td>%s</td><td>%s</td></tr>' % (_u(job["job_id"]), _e(job["label"]), _chip(job["status"]), _e(job["epoch_id"] or "Workspace"))
            for job in recent
        ) or '<tr><td class="empty" colspan="3">No operation has run in this portal process.</td></tr>'
        disabled = "disabled" if not readiness["ready"] else ""
        body = f"""<section class="hero"><div class="eyebrow" style="color:#aebfff">Local-first benchmark operations</div><h1>Build, test, and run epochs from one workspace.</h1><p class="subtitle">Check Ubuntu and Google Cloud readiness, launch local diagnostics, and follow each paid epoch through preflight, evaluation, review, publication, and sealed audit.</p><div class="hero-actions"><a class="button ghost" href="#new-epoch">Create an epoch</a><a class="button ghost" href="#diagnostics">Run diagnostics</a></div></section>
<section class="section grid"><article class="card span-7"><div class="section-head"><div><div class="eyebrow">Readiness</div><h2>{'Ready to create epochs' if readiness['ready'] else 'Action required before paid work'}</h2></div>{_chip('passed' if readiness['ready'] else 'failed')}</div><div class="readiness">{check_html}</div></article><article class="card span-5" id="new-epoch"><div class="eyebrow">New epoch</div><h2>Freeze a new experiment</h2><p class="subtitle">Uses {_e(readiness['catalog_version'])} with {_e(readiness['model_count'])} serverless routes.</p><form class="stack section" method="post" action="/epochs">{_csrf(request.app.state.csrf_token)}<div class="field"><label for="epoch-id">Epoch ID</label><input id="epoch-id" name="epoch_id" value="{_e(new_epoch_id())}" pattern="[a-zA-Z0-9._-]+" required></div><button class="button" type="submit" {disabled}>Create immutable epoch</button><p class="hint">Evidence root: <span class="mono">{_e(readiness['evidence_root'])}</span></p></form></article></section>
<section class="section" id="diagnostics"><div class="section-head"><div><div class="eyebrow">Local testing</div><h2>Diagnostics</h2></div><span class="muted">Python {_e(readiness['python'])} · ADK {_e(readiness['google_adk'])}</span></div><div class="card"><div class="button-row">{diagnostics}</div><p class="hint" style="margin-top:12px">Diagnostics share the serialized operation queue with epoch mutations.</p></div></section>
<section class="section"><div class="section-head"><h2>Epochs</h2><span class="muted">{len(epochs)} saved</span></div><div class="grid">{epoch_cards}</div></section><section class="section"><div class="section-head"><h2>Recent operations</h2><a href="/jobs">View all</a></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Scope</th></tr></thead><tbody>{jobs}</tbody></table></div></section>"""
        return _layout("Workspace", body)

    @app.post("/diagnostics/{action}")
    def diagnostic(request: Request, action: str, csrf_token: str = Form(...)):
        try:
            verify_csrf(csrf_token)
            job = console.run_diagnostic(action)
            return RedirectResponse("/jobs/%s" % job.job_id, status_code=303)
        except Exception as exc:
            return fail("Could not start the diagnostic", str(exc))

    @app.get("/epochs/{epoch_id}", response_class=HTMLResponse)
    def epoch_control(request: Request, epoch_id: str):
        try:
            value = console.epoch(epoch_id)
        except Exception as exc:
            return fail("Could not read the epoch", str(exc), 404)
        active_job = next((item for item in console.jobs.recent() if item["epoch_id"] == epoch_id and item["status"] in {"queued", "running"}), None)
        workflow = "".join(
            '<div class="workflow-step %s"><div class="index">Step %d</div><strong>%s</strong><div class="mini">%s</div></div>' % (
                "complete" if step["complete"] else "current" if step["current"] else "", index, _e(step["label"]), "Complete" if step["complete"] else "Next" if step["current"] else "Pending",
            )
            for index, step in enumerate(value["workflow"], 1)
        )
        cards = []
        for action, label in console.EPOCH_ACTIONS.items():
            state = value["action_states"][action]
            disabled_action = bool(active_job) or not state["enabled"]
            cards.append(
                '<form class="card span-4 action-card %s" method="post" action="%s/actions/%s">%s<div class="badge-line">%s</div><h3>%s</h3><p>%s</p><label class="check"><input type="checkbox" name="confirmed" value="yes" required %s><span>Confirm this operation</span></label><button class="button %s" type="submit" %s>%s</button></form>' % (
                    "current" if state["enabled"] and not state["complete"] else "", _epoch_link(epoch_id), _u(action), _csrf(request.app.state.csrf_token), _chip("passed" if state["complete"] else "current" if state["enabled"] else "locked"), _e(label), _e(state["hint"]), "disabled" if disabled_action else "", "danger" if action == "audit" else "secondary", "disabled" if disabled_action else "", _e(label),
                )
            )
        candidate_rows = "".join(
            '<tr><td><a href="%s/candidates/%s">%s</a><br><span class="mono muted">%s…</span></td><td class="num">%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>' % (
                _epoch_link(epoch_id), _u(item["snapshot_id"]), _e(item["model"]), _e(item["snapshot_id"][:32]), item["round"], _chip(item["status"]), item["solver_cells"], _percent(item["best_solver_median"]), item["certificate_count"],
            )
            for item in value["candidates"]
        ) or '<tr><td class="empty" colspan="6">No candidate has been frozen yet.</td></tr>'
        failed = value.get("failed_work", [])
        failed_html = "" if not failed else '<section class="notice error section"><strong>Failed work needs attention.</strong><ul>%s</ul></section>' % "".join('<li><span class="mono">%s</span>: %s</li>' % (_e(item["work_id"]), _e(item.get("error", "unknown error"))) for item in failed)
        active_html = "" if not active_job else '<div class="notice"><strong>%s is %s.</strong> <a href="/jobs/%s">Open live output</a>.</div>' % (_e(active_job["label"]), _e(active_job["status"]), _u(active_job["job_id"]))
        review_html = ""
        if value["review_open"] and value["phase"] == "awaiting_review":
            review_html = '<div class="notice success"><strong>Review window is open.</strong> Open final-round candidates to add evidence and signed decisions.</div>'
        elif not value["review_open"]:
            review_html = '<div class="notice warn"><strong>Review inputs are frozen.</strong> Certificates and decisions are read-only.</div>'
        manifest = value["manifest"]
        usage = value["usage"]
        cost_limit = value.get("max_estimated_cost_usd") or 0.0
        body = f"""<div class="breadcrumb"><a href="/">Workspace</a> / {_e(epoch_id)}</div><div class="page-head"><div><div class="eyebrow">{_e(manifest['catalog_version'])}</div><h1>{_e(epoch_id)}</h1><p class="subtitle">{manifest['models']} models · {manifest['rounds']} creator rounds · {manifest['solver_repetitions']} repetitions · {_e(manifest['gcp_location'])} routing</p></div>{_chip(value['phase'])}</div>{active_html}{review_html}
<section class="section"><div class="section-head"><h2>Workflow</h2><span class="muted">Phase-aware controls prevent out-of-order operations</span></div><div class="workflow">{workflow}</div></section><section class="section grid"><article class="card span-3"><div class="metric-label">Snapshots</div><div class="metric">{value['snapshots']}</div></article><article class="card span-3"><div class="metric-label">Solver cells</div><div class="metric">{value['solver_cells']}</div></article><article class="card span-3"><div class="metric-label">Model calls</div><div class="metric">{usage['calls']}</div></article><article class="card span-3"><div class="metric-label">Conservative cost</div><div class="metric">${usage['estimated_cost_usd']:.2f}</div><div class="mini">Limit ${cost_limit:.2f}</div></article></section>{failed_html}
<section class="section"><div class="section-head"><h2>Epoch controls</h2><div class="button-row"><a class="button secondary" href="{_epoch_link(epoch_id)}/observability">Agent activity</a><a class="button secondary" href="{_epoch_link(epoch_id)}/results">Rankings</a></div></div><div class="grid">{''.join(cards)}</div></section><section class="section"><div class="section-head"><h2>Candidate benchmarks</h2><span class="muted">{len(value['candidates'])} snapshots</span></div><div class="table-wrap"><table><thead><tr><th>Candidate</th><th class="num">Round</th><th>Status</th><th class="num">Cells</th><th class="num">Best solver</th><th class="num">Certificates</th></tr></thead><tbody>{candidate_rows}</tbody></table></div></section>"""
        return _layout(epoch_id, body)

    @app.get("/jobs", response_class=HTMLResponse)
    def operations() -> str:
        rows = "".join(
            '<tr><td><a href="/jobs/%s">%s</a></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (_u(job["job_id"]), _e(job["label"]), _chip(job["status"]), _e(job["epoch_id"] or "Workspace"), _e(job["created_at"]), _e(job["finished_at"] or "—"))
            for job in console.jobs.recent(50)
        ) or '<tr><td class="empty" colspan="5">No operation has run in this portal process.</td></tr>'
        body = '<div class="page-head"><div><div class="eyebrow">Serialized local execution</div><h1>Operations</h1><p class="subtitle">Diagnostics and epoch mutations share one queue.</p></div></div><div class="table-wrap"><table><thead><tr><th>Operation</th><th>Status</th><th>Scope</th><th>Started</th><th>Finished</th></tr></thead><tbody>%s</tbody></table></div>' % rows
        return _layout("Operations", body, current="jobs")

    return app
