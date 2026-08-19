"""Localhost-only web console and spatial command deck for BBA operators."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from bba.operator import OperatorConsole
from bba.visualizer import VisualizerStateSerializer


SYSTEM_MAP_PATH = Path(__file__).resolve().parent / "data" / "system_map.html"

FINDING_LABELS = {
    "named_capability_valid": "The named capability is valid.",
    "public_materials_sufficient": "The public materials are sufficient.",
    "oracle_consistent": "The oracle is consistent.",
    "scorer_consistent": "The scorer is consistent.",
    "no_arbitrary_obscurity": "The benchmark has no arbitrary obscurity.",
    "useful_evaluation": "The benchmark is a useful evaluation.",
    "solvability_certificate_adequate": "The solvability certificate is adequate.",
}


def _is_allowed_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return True
    h = hostname.lower()
    if h in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return True
    if h.endswith((".c.googlers.com", ".corp.google.com", ".googlers.com", ".google.com")):
        return True
    return False


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

    dist_dir = Path(__file__).resolve().parent / "data" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/dist", StaticFiles(directory=str(dist_dir), html=False), name="dist")

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            "localhost",
            "127.0.0.1",
            "[::1]",
            "testserver",
            "*.c.googlers.com",
            "*.corp.google.com",
            "*.googlers.com",
            "*.google.com",
        ],
    )

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and not _is_allowed_host(urlparse(origin).hostname):
            return HTMLResponse("Untrusted origin", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; connect-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
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

    # --- REACT SPA ENTRYPOINT ROUTES ---
    @app.get("/", response_class=HTMLResponse)
    @app.get("/jobs", response_class=HTMLResponse)
    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    @app.get("/epochs", response_class=HTMLResponse)
    @app.get("/epochs/{epoch_id}", response_class=HTMLResponse)
    @app.get("/epochs/{epoch_id}/{path:path}", response_class=HTMLResponse)
    def spa_page():
        if SYSTEM_MAP_PATH.is_file():
            return HTMLResponse(SYSTEM_MAP_PATH.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>BBA Operator Console</h1><p>Frontend assets not found.</p>", status_code=404)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    # --- REACTIVE JSON API ENDPOINTS ---
    @app.get("/api/system/state")
    def api_system_state():
        data = VisualizerStateSerializer.serialize_system_state(console)
        data["csrf_token"] = app.state.csrf_token
        return JSONResponse(data)

    @app.get("/api/epoch/{epoch_id}/state")
    def api_epoch_state(epoch_id: str):
        try:
            return JSONResponse(VisualizerStateSerializer.serialize_epoch_state(console, epoch_id))
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/epoch/{epoch_id}/candidate/{snapshot_id}")
    @app.get("/api/epoch/{epoch_id}/candidates/{snapshot_id}")
    def api_candidate_details(epoch_id: str, snapshot_id: str):
        try:
            return JSONResponse(VisualizerStateSerializer.serialize_candidate_details(console, epoch_id, snapshot_id))
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/epoch/{epoch_id}/action")
    @app.post("/api/epoch/{epoch_id}/action/{action}")
    async def api_epoch_action(request: Request, epoch_id: str, action: Optional[str] = None):
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                payload = await request.json()
            else:
                form = await request.form()
                payload = dict(form)

            act = action or str(payload.get("action", ""))
            verify(request, str(payload.get("csrf_token", "")), str(payload.get("confirmed", "")))

            if act in getattr(console, "EPOCH_ACTIONS", {}):
                job = console.run_epoch_action(epoch_id, act)
            elif act in getattr(console, "DIAGNOSTIC_ACTIONS", {}):
                job = console.run_diagnostic(act)
            elif act == "pause":
                job = console.run_epoch_action(epoch_id, "close") if "close" in getattr(console, "EPOCH_ACTIONS", {}) else None
                if not job:
                    return JSONResponse({"status": "ok", "job_id": "paused", "label": "Pause Tournament"})
            elif act == "step":
                job = console.run_epoch_action(epoch_id, "run")
            else:
                raise ValueError(f"Unknown action '{act}'. Available: {list(getattr(console, 'EPOCH_ACTIONS', {}).keys())}")

            return JSONResponse({"status": "ok", "job_id": getattr(job, "job_id", "done"), "label": getattr(job, "label", act)})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    @app.post("/api/epoch/{epoch_id}/action/certificate")
    @app.post("/api/epoch/{epoch_id}/candidates/{snapshot_id}/certificate")
    async def api_record_certificate(request: Request, epoch_id: str, snapshot_id: Optional[str] = None):
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                payload = await request.json()
            else:
                form = await request.form()
                payload = dict(form)

            verify(request, str(payload.get("csrf_token", "")), str(payload.get("confirmed", "")))
            snap_id = snapshot_id or str(payload.get("snapshot_id", ""))
            job = console.record_certificate(
                epoch_id,
                snap_id,
                str(payload.get("certificate_type", "")),
                str(payload.get("issuer_id", "")),
                str(payload.get("independence_basis", "")),
                str(payload.get("verification_method", "")),
                str(payload.get("scope", "")),
                str(payload.get("evidence_lines", "")),
                str(payload.get("answers_path", "")),
            )
            return JSONResponse({"status": "ok", "job_id": job.job_id, "label": job.label})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    @app.post("/api/epoch/{epoch_id}/action/review")
    @app.post("/api/epoch/{epoch_id}/candidates/{snapshot_id}/review")
    async def api_record_review(request: Request, epoch_id: str, snapshot_id: Optional[str] = None):
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                payload = await request.json()
            else:
                form = await request.form()
                payload = dict(form)

            verify(request, str(payload.get("csrf_token", "")), str(payload.get("confirmed", "")))
            findings_raw = payload.get("findings", {})
            if isinstance(findings_raw, dict):
                finding_values = {name: bool(findings_raw.get(name)) for name in FINDING_LABELS}
            else:
                finding_values = {name: bool(payload.get(name)) for name in FINDING_LABELS}

            snap_id = snapshot_id or str(payload.get("snapshot_id", ""))
            job = console.record_review(
                epoch_id,
                snap_id,
                str(payload.get("reviewer_id", "")),
                str(payload.get("certificate_digest", "")),
                str(payload.get("decision", "")),
                finding_values,
                str(payload.get("limitations", "")),
                str(payload.get("key_id", "")),
                str(payload.get("signing_key_path", "")),
                str(payload.get("public_key_path", "")),
                str(payload.get("prior_review_digest", "")),
            )
            return JSONResponse({"status": "ok", "job_id": job.job_id, "label": job.label})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    @app.get("/api/jobs")
    def api_jobs():
        recent = console.jobs.recent(50)
        return JSONResponse({"jobs": recent})

    @app.get("/api/jobs/{job_id}")
    def api_job_detail(job_id: str):
        job = console.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Operation not found")
        return JSONResponse(job)

    @app.post("/api/diagnostics/{action}")
    async def api_diagnostic(request: Request, action: str):
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                payload = await request.json()
            else:
                form = await request.form()
                payload = dict(form)

            verify(request, str(payload.get("csrf_token", "")))
            job = console.run_diagnostic(action)
            return JSONResponse({"status": "ok", "job_id": job.job_id, "label": job.label})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    @app.post("/api/epochs")
    @app.post("/api/epoch")
    async def api_create_epoch(request: Request):
        try:
            if request.headers.get("content-type", "").startswith("application/json"):
                payload = await request.json()
            else:
                form = await request.form()
                payload = dict(form)

            verify(request, str(payload.get("csrf_token", "")))
            epoch_id = str(payload.get("epoch_id", "")).strip()
            job = console.create_epoch(epoch_id)
            return JSONResponse({"status": "ok", "job_id": job.job_id, "label": job.label, "epoch_id": epoch_id})
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

    return app


def get_app(evidence_root: Optional[Path] = None) -> FastAPI:
    """Create default FastAPI application instance for uvicorn reload."""
    root = evidence_root or Path(os.environ.get("BBA_EVIDENCE", ".bba")).resolve()
    console = OperatorConsole(root)
    return create_app(console)


app = get_app()


def run_console(evidence_root: Path, port: int = 8765, reload: bool = False) -> None:
    """Run the operator console on the IPv4 loopback interface."""
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    os.environ["BBA_EVIDENCE"] = str(evidence_root)
    if reload:
        uvicorn.run("bba.web:app", host="127.0.0.1", port=port, log_level="info", reload=True)
    else:
        console = OperatorConsole(evidence_root)
        uvicorn.run(create_app(console), host="127.0.0.1", port=port, log_level="info")
