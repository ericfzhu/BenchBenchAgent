"""Phase-aware candidate review page for the local development portal."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from bba import _web as legacy
from bba._portal import (
    _chip,
    _csrf,
    _drop_get_route,
    _e,
    _epoch_link,
    _layout,
    _u,
)
from bba.protocol import PromotionDecision, SolvabilityCertificateType


def _percent(value) -> str:
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def install_candidate_page(app, console):
    path = "/epochs/{epoch_id}/candidates/{snapshot_id}"
    _drop_get_route(app, path)

    def fail(title: str, message: str, status: int = 400) -> HTMLResponse:
        body = f'<div class="page-head"><div><div class="eyebrow">Request stopped</div><h1>{_e(title)}</h1><p class="subtitle">{_e(message)}</p></div></div><a class="button secondary" href="/">Return to workspace</a>'
        return HTMLResponse(_layout(title, body), status_code=status)

    @app.get(path, response_class=HTMLResponse)
    def candidate_page(request: Request, epoch_id: str, snapshot_id: str):
        try:
            item = console.candidate(epoch_id, snapshot_id)
        except Exception as exc:
            return fail("Could not read the candidate", str(exc), 404)

        certificates = "".join(
            f'<option value="{_e(cert["digest"])}">{_e(cert["certificate_type"].replace("_", " "))} · {_e(cert["digest"][:16])}…</option>'
            for cert in item["certificates"]
        )
        certificate_list = "".join(
            f'<li><span class="mono">{_e(cert["digest"])}</span><br><span class="muted">{_e(cert["certificate_type"].replace("_", " "))} by {_e(cert["issuer_id"])}</span></li>'
            for cert in item["certificates"]
        ) or '<li class="muted">No solvability certificate exists.</li>'
        promotion_list = "".join(
            f'<li>{_chip(record["decision"])} <span class="muted">by {_e(record["reviewer_id"])}</span></li>'
            for record in item["promotions"]
        ) or '<li class="muted">No signed decision exists.</li>'
        type_options = "".join(
            f'<option value="{_e(value.value)}">{_e(value.value.replace("_", " "))}</option>'
            for value in SolvabilityCertificateType
        )
        decision_options = "".join(
            f'<option value="{_e(value.value)}">{_e(value.value)}</option>'
            for value in PromotionDecision
        )
        findings = "".join(
            f'<label class="check"><input type="checkbox" name="{_e(name)}" value="yes"><span>{_e(label)}</span></label>'
            for name, label in legacy.FINDING_LABELS.items()
        )

        eligible = bool(item["final_round"] and item.get("review_open", True))
        disabled = "" if eligible else "disabled"
        if not item["final_round"]:
            review_notice = '<div class="notice warn"><strong>Historical snapshot.</strong> Only final-round candidates can receive solvability evidence or a canonical decision.</div>'
        elif not item.get("review_open", True):
            review_notice = '<div class="notice warn"><strong>Review inputs are frozen.</strong> The audit population has been committed, so certificates and decisions are now read-only.</div>'
        else:
            review_notice = '<div class="notice success"><strong>Review window open.</strong> Independent evidence and signed decisions can be recorded until the audit population is frozen.</div>'

        body = f"""<div class="breadcrumb"><a href="/">Workspace</a> / <a href="{_epoch_link(epoch_id)}">{_e(epoch_id)}</a> / Candidate</div><div class="page-head"><div><div class="eyebrow">Creator round {item['round']}</div><h1>{_e(item['model'])}</h1><p class="subtitle mono">{_e(item['snapshot_id'])}</p></div>{_chip(item['status'])}</div>{review_notice}
<section class="section grid"><article class="card span-5"><div class="eyebrow">Immutable evidence</div><h2>Candidate record</h2><dl class="definition"><dt>Design digest</dt><dd class="mono">{_e(item['design_digest'])}</dd><dt>Best solver</dt><dd>{_percent(item['best_solver_median'])}</dd><dt>Panel median</dt><dd>{_percent(item['panel_median'])}</dd><dt>Solver cells</dt><dd>{item['solver_cells']}</dd><dt>Review state</dt><dd>{'Reviewed' if item['reviewed'] else 'Not reviewed'}</dd></dl><h3 class="section">Certificates</h3><ul>{certificate_list}</ul><h3 class="section">Signed decisions</h3><ul>{promotion_list}</ul></article>
<article class="card span-7"><div class="eyebrow">Step 1</div><h2>Record solvability evidence</h2><p class="subtitle">BBA copies each evidence file into immutable local evidence. Use absolute local paths and keep private keys outside the evidence root.</p><form class="stack section" method="post" action="{_epoch_link(epoch_id)}/candidates/{_u(snapshot_id)}/certificate">{_csrf(request.app.state.csrf_token)}<div class="field"><label>Certificate type</label><select name="certificate_type" {disabled}>{type_options}</select></div><div class="field"><label>Issuer ID</label><input name="issuer_id" required {disabled}></div><div class="field"><label>Independence basis</label><textarea name="independence_basis" required {disabled}></textarea></div><div class="field"><label>Verification method</label><textarea name="verification_method" required {disabled}></textarea></div><div class="field"><label>Scope</label><input name="scope" required {disabled}></div><div class="field"><label>Evidence files</label><textarea class="mono" name="evidence_lines" placeholder="working-notes.md=/absolute/path/working-notes.md" required {disabled}></textarea><p class="hint">Use one NAME=/absolute/path line for each file.</p></div><div class="field"><label>Answers JSON path</label><input name="answers_path" placeholder="Required only for human reconstruction" {disabled}><p class="hint">Selected item IDs: <span class="mono">{_e(', '.join(item['certificate_item_ids']) or 'not available')}</span></p></div><label class="check"><input type="checkbox" name="confirmed" value="yes" required {disabled}><span>I confirm that the issuer is independent from the creator.</span></label><button class="button" type="submit" {disabled}>Record certificate</button></form></article></section>
<section class="card section"><div class="eyebrow">Step 2</div><h2>Record a signed candidate decision</h2><p class="subtitle">Approval requires every finding to pass. The approving reviewer must differ from the certificate issuer.</p><form class="stack section" method="post" action="{_epoch_link(epoch_id)}/candidates/{_u(snapshot_id)}/review">{_csrf(request.app.state.csrf_token)}<div class="grid"><div class="field span-4"><label>Decision</label><select name="decision" {disabled}>{decision_options}</select></div><div class="field span-4"><label>Reviewer ID</label><input name="reviewer_id" required {disabled}></div><div class="field span-4"><label>Certificate</label><select name="certificate_digest" required {disabled}><option value="">Select a certificate</option>{certificates}</select></div></div><fieldset {disabled}><legend>Construct validity findings</legend><div class="grid">{findings}</div></fieldset><div class="grid"><div class="field span-4"><label>Key ID</label><input name="key_id" required {disabled}></div><div class="field span-4"><label>Private key path</label><input name="signing_key_path" required {disabled}></div><div class="field span-4"><label>Public key path</label><input name="public_key_path" required {disabled}></div></div><div class="field"><label>Limitations</label><textarea name="limitations" placeholder="Use one limitation per line." {disabled}></textarea></div><div class="field"><label>Prior escalated review digest</label><input name="prior_review_digest" placeholder="Required only for a second review" {disabled}></div><label class="check"><input type="checkbox" name="confirmed" value="yes" required {disabled}><span>I confirm that this decision will become immutable signed evidence.</span></label><button class="button" type="submit" {disabled}>Record signed decision</button></form></section>"""
        return _layout(f"Candidate {item['model']}", body)

    return app
