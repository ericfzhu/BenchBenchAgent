"""Discover effective Vertex quotas and pace BBA model calls safely."""
from __future__ import annotations

import json, math, os, sqlite3, threading, time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4

import google.auth
from google.auth.transport.requests import AuthorizedSession
from bba.protocol import ModelIdentity

CLOUD_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
SERVICE_USAGE_URL = "https://serviceusage.googleapis.com/v1beta1"
DEFAULT_UTILIZATION = 2 / 3
WINDOW_SECONDS = 60.0
GROK_METRICS = {
    "requests": "global_generate_content_requests_per_minute_per_project_per_base_model",
    "input": "global_generate_content_input_tokens_per_minute_per_base_model",
    "output": "global_generate_content_output_tokens_per_minute_per_base_model",
}
CLAUDE_METRICS = {
    "requests": "global_online_prediction_requests_per_base_model",
    "input": "global_online_prediction_input_tokens_per_minute_per_base_model",
    "output": "global_online_prediction_output_tokens_per_minute_per_base_model",
    "combined": "global_online_prediction_tokens_per_minute_per_base_model",
}
SHARED_CLAUDE = {
    "claude-sonnet-5": "anthropic-claude-sonnet",
    "claude-opus-5": "anthropic-claude-opus",
    "claude-fable-5": "anthropic-claude-fable",
    "claude-opus-4-8": "anthropic-claude-opus",
}

class QuotaDiscoveryError(RuntimeError):
    pass

@dataclass(frozen=True)
class ModelQuotaPolicy:
    project: str
    location: str
    bucket: str
    mode: str
    provider_requests_per_minute: int | None
    provider_input_tokens_per_minute: int | None
    provider_output_tokens_per_minute: int | None
    effective_requests_per_minute: int | None
    effective_input_tokens_per_minute: int | None
    effective_output_tokens_per_minute: int | None
    utilization: float
    metrics: Mapping[str, str]

    @property
    def fixed(self): return self.mode == "fixed"

    @property
    def minimum_spacing_seconds(self):
        return 60 / self.effective_requests_per_minute if self.effective_requests_per_minute else 0.0

@dataclass(frozen=True)
class QuotaSnapshot:
    project: str
    location: str
    discovered_at: str
    utilization: float
    policies: Mapping[str, ModelQuotaPolicy]

    def to_primitive(self):
        return {
            "schema_version": 1, "project": self.project, "location": self.location,
            "discovered_at": self.discovered_at, "utilization": self.utilization,
            "policies": {k: asdict(v) for k, v in sorted(self.policies.items())},
        }

def quota_base_model(identity):
    if identity.publisher == "xai": return identity.model
    if identity.publisher == "anthropic":
        return SHARED_CLAUDE.get(identity.model, f"anthropic-{identity.model}")
    return identity.model

def quota_mode(identity):
    return "fixed" if identity.publisher in {"xai", "anthropic"} else "adaptive"

def _family(identity):
    if identity.publisher == "xai": return GROK_METRICS
    if identity.publisher == "anthropic": return CLAUDE_METRICS
    return {}

def _target(value, utilization):
    if value is None or value < 0: return None
    if value == 0: return 0
    return max(1, math.floor(value * utilization))

class VertexQuotaDiscovery:
    """Read project-specific effective limits from Service Usage."""
    def __init__(self, project, *, location="global", utilization=DEFAULT_UTILIZATION,
                 credentials_loader=None, session_factory=AuthorizedSession):
        if not 0.1 <= utilization <= 0.95:
            raise ValueError("quota utilization must be between 0.10 and 0.95")
        self.project, self.location, self.utilization = project, location, float(utilization)
        self.credentials_loader = credentials_loader or google.auth.default
        self.session_factory = session_factory

    def _metrics(self):
        credentials, _ = self.credentials_loader(scopes=[CLOUD_SCOPE])
        session = self.session_factory(credentials)
        url = (f"{SERVICE_USAGE_URL}/projects/{self.project}/services/"
               "aiplatform.googleapis.com/consumerQuotaMetrics")
        params, result = {"view": "FULL", "pageSize": 200}, []
        while True:
            response = session.get(url, params=params, timeout=30)
            if response.status_code >= 400:
                raise QuotaDiscoveryError(
                    "could not read Vertex quotas; grant serviceusage.quotas.get "
                    f"on {self.project}: HTTP {response.status_code} {response.text[:400]}"
                )
            payload = response.json(); result.extend(payload.get("metrics", ()))
            token = payload.get("nextPageToken")
            if not token: return result
            params["pageToken"] = token

    @staticmethod
    def _limits(metrics):
        result, ranks = {}, {}
        for metric in metrics:
            name = str(metric.get("metric", "")).rsplit("/", 1)[-1]
            for limit in metric.get("consumerQuotaLimits", ()) or ():
                for bucket in limit.get("quotaBuckets", ()) or ():
                    dims = dict(bucket.get("dimensions", {}) or {})
                    base = dims.get("base_model")
                    region = dims.get("location") or dims.get("region")
                    if not base or (region and region != "global"): continue
                    try: value = int(bucket.get("effectiveLimit"))
                    except (TypeError, ValueError): continue
                    key, rank = (name, str(base)), len(dims)
                    if rank >= ranks.get(key, -1): result[key], ranks[key] = value, rank
        return result

    def discover(self, identities: Iterable[ModelIdentity]):
        identities, values = tuple(identities), self._limits(self._metrics())
        policies = {}
        for identity in identities:
            mode, bucket, family = quota_mode(identity), quota_base_model(identity), _family(identity)
            rpm = inp = out = None; used = {}
            if mode == "fixed":
                rpm = values.get((family["requests"], bucket))
                inp = values.get((family["input"], bucket))
                out = values.get((family["output"], bucket))
                used = {k: family[k] for k in ("requests", "input", "output")}
                combined = values.get((family.get("combined"), bucket)) if family.get("combined") else None
                if combined is not None and inp is None and out is None:
                    inp = out = combined; used["combined"] = family["combined"]
                missing = [k for k, v in (("requests", rpm), ("input", inp), ("output", out)) if v is None]
                if missing:
                    raise QuotaDiscoveryError(
                        f"missing {', '.join(missing)} quota for base_model={bucket}; "
                        "verify model access and quota-viewer permission"
                    )
                if min(rpm, inp, out) <= 0:
                    raise QuotaDiscoveryError(
                        f"zero Vertex quota for base_model={bucket}: rpm={rpm}, input_tpm={inp}, output_tpm={out}"
                    )
            policies[identity.artifact_id] = ModelQuotaPolicy(
                self.project, self.location, bucket, mode, rpm, inp, out,
                _target(rpm, self.utilization), _target(inp, self.utilization),
                _target(out, self.utilization), self.utilization, used,
            )
        return QuotaSnapshot(
            self.project, self.location, datetime.now(timezone.utc).isoformat(),
            self.utilization, policies,
        )

class QuotaGovernor:
    """Shared rolling-window governor for every model call under one evidence root."""
    def __init__(self, root, project, *, location="global", utilization=DEFAULT_UTILIZATION,
                 refresh_seconds=300, discovery=None, clock=time.time, sleeper=time.sleep):
        self.root = Path(root).resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.project, self.location, self.utilization = project, location, float(utilization)
        self.refresh_seconds = max(30, int(refresh_seconds)); self.clock, self.sleeper = clock, sleeper
        self.discovery = discovery or VertexQuotaDiscovery(project, location=location, utilization=utilization)
        self.path = self.root / "quota-governor.sqlite3"; self._guard = threading.Lock()
        self._snapshot = None; self._snapshot_at = 0.0; self._init_db()

    @classmethod
    def from_environment(cls, root):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("VERTEXAI_PROJECT")
        if not project: raise QuotaDiscoveryError("GOOGLE_CLOUD_PROJECT is required for quota governance")
        return cls(
            root, project,
            location=os.environ.get("GOOGLE_CLOUD_LOCATION") or os.environ.get("VERTEXAI_LOCATION") or "global",
            utilization=float(os.environ.get("BBA_QUOTA_UTILIZATION", DEFAULT_UTILIZATION)),
            refresh_seconds=int(os.environ.get("BBA_QUOTA_REFRESH_SECONDS", "300")),
        )

    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=30); db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL"); return db

    def _init_db(self):
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS quota_snapshots(project TEXT, location TEXT, payload TEXT, observed_at REAL, PRIMARY KEY(project,location));
            CREATE TABLE IF NOT EXISTS quota_events(lease_id TEXT PRIMARY KEY, project TEXT, bucket TEXT, started_at REAL, reserved_input INTEGER, reserved_output INTEGER, actual_input INTEGER, actual_output INTEGER, status TEXT);
            CREATE INDEX IF NOT EXISTS quota_events_bucket_time ON quota_events(project,bucket,started_at);
            CREATE TABLE IF NOT EXISTS quota_cooldowns(project TEXT, bucket TEXT, until_at REAL, penalty INTEGER, PRIMARY KEY(project,bucket));
            """)

    @staticmethod
    def _decode(payload):
        policies = {k: ModelQuotaPolicy(**v) for k, v in payload.get("policies", {}).items()}
        return QuotaSnapshot(payload["project"], payload["location"], payload["discovered_at"], payload["utilization"], policies)

    def _load(self):
        with self._db() as db:
            row = db.execute("SELECT payload,observed_at FROM quota_snapshots WHERE project=? AND location=?", (self.project,self.location)).fetchone()
        return (None, 0.0) if row is None else (self._decode(json.loads(row["payload"])), float(row["observed_at"]))

    def refresh(self, identities, *, force=False):
        identities, now = tuple(identities), self.clock()
        with self._guard:
            if self._snapshot is None: self._snapshot, self._snapshot_at = self._load()
            cached = self._snapshot; needed = {i.artifact_id for i in identities}
            covers = cached is not None and needed.issubset(cached.policies)
            if covers and not force and now - self._snapshot_at < self.refresh_seconds: return cached
            try: fresh = self.discovery.discover(identities)
            except Exception:
                if covers and now - self._snapshot_at < self.refresh_seconds * 6: return cached
                raise
            if cached is not None:
                merged = dict(cached.policies); merged.update(fresh.policies)
                fresh = QuotaSnapshot(fresh.project, fresh.location, fresh.discovered_at, fresh.utilization, merged)
            payload = json.dumps(fresh.to_primitive(), sort_keys=True, separators=(",", ":"))
            with self._db() as db:
                db.execute("INSERT INTO quota_snapshots VALUES(?,?,?,?) ON CONFLICT(project,location) DO UPDATE SET payload=excluded.payload,observed_at=excluded.observed_at", (self.project,self.location,payload,now))
            self._snapshot, self._snapshot_at = fresh, now; return fresh

    def policy(self, identity): return self.refresh((identity,)).policies[identity.artifact_id]

    @staticmethod
    def estimate_input_tokens(request):
        try: value = request.model_dump(mode="json", by_alias=False, exclude_none=True)
        except Exception: value = str(request)
        raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
        return max(1, math.ceil(len(raw) / 3) + 256)

    def output_cap(self, identity, requested):
        policy = self.policy(identity)
        return requested if not policy.fixed else max(1, min(requested, policy.effective_output_tokens_per_minute))

    def acquire(self, identity, estimated_input, reserved_output):
        policy, lease = self.policy(identity), uuid4().hex
        while True:
            wait = self._attempt(policy, lease, estimated_input, reserved_output)
            if wait <= 0: return lease
            self.sleeper(min(wait, 60.0))

    def _attempt(self, policy, lease, estimated_input, reserved_output):
        now = self.clock()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE"); db.execute("DELETE FROM quota_events WHERE project=? AND started_at<=?", (self.project, now-60))
            cooldown = db.execute("SELECT until_at FROM quota_cooldowns WHERE project=? AND bucket=?", (self.project,policy.bucket)).fetchone()
            if cooldown and cooldown["until_at"] > now: db.rollback(); return cooldown["until_at"]-now
            if not policy.fixed:
                db.execute("INSERT INTO quota_events VALUES(?,?,?,?,?,?,NULL,NULL,'in_flight')", (lease,self.project,policy.bucket,now,estimated_input,reserved_output)); db.commit(); return 0
            if estimated_input > policy.provider_input_tokens_per_minute:
                db.rollback(); raise RuntimeError(f"one {policy.bucket} request estimate ({estimated_input}) exceeds provider input TPM ({policy.provider_input_tokens_per_minute})")
            if reserved_output > policy.provider_output_tokens_per_minute:
                db.rollback(); raise RuntimeError(f"one {policy.bucket} output reservation ({reserved_output}) exceeds provider output TPM ({policy.provider_output_tokens_per_minute})")
            rows = db.execute("SELECT * FROM quota_events WHERE project=? AND bucket=? ORDER BY started_at", (self.project,policy.bucket)).fetchall()
            used_in = sum(int(r["actual_input"] if r["actual_input"] is not None else r["reserved_input"]) for r in rows)
            used_out = sum(int(r["actual_output"] if r["actual_output"] is not None else r["reserved_output"]) for r in rows)
            spacing = max(0.0, rows[-1]["started_at"] + policy.minimum_spacing_seconds - now) if rows else 0
            target_in, target_out = policy.effective_input_tokens_per_minute, policy.effective_output_tokens_per_minute
            allowed = (len(rows) < policy.effective_requests_per_minute and spacing <= 0
                       and (used_in + estimated_input <= target_in or (not rows and estimated_input <= policy.provider_input_tokens_per_minute))
                       and (used_out + reserved_output <= target_out or (not rows and reserved_output <= policy.provider_output_tokens_per_minute)))
            if allowed:
                db.execute("INSERT INTO quota_events VALUES(?,?,?,?,?,?,NULL,NULL,'in_flight')", (lease,self.project,policy.bucket,now,estimated_input,reserved_output)); db.commit(); return 0
            waits = ([spacing] if spacing > 0 else []) + [max(.05, r["started_at"]+60-now) for r in rows]
            db.rollback(); return min(waits) if waits else 1.0

    def reconcile(self, lease, input_tokens, output_tokens):
        if not lease: return
        with self._db() as db:
            row = db.execute("SELECT bucket FROM quota_events WHERE lease_id=?", (lease,)).fetchone()
            if not row: return
            db.execute("UPDATE quota_events SET actual_input=?,actual_output=?,status='success' WHERE lease_id=?", (max(0,int(input_tokens)),max(0,int(output_tokens)),lease))
            db.execute("DELETE FROM quota_cooldowns WHERE project=? AND bucket=?", (self.project,row["bucket"]))

    @staticmethod
    def _is_quota_error(error):
        text = f"{type(error).__name__}: {error}".lower()
        return any(x in text for x in ("429","resource_exhausted","resource exhausted","quota","too many requests"))

    def fail(self, lease, error):
        if not lease: return
        now = self.clock()
        with self._db() as db:
            row = db.execute("SELECT bucket FROM quota_events WHERE lease_id=?", (lease,)).fetchone()
            if not row: return
            bucket = row["bucket"]; db.execute("UPDATE quota_events SET actual_input=0,actual_output=0,status='error' WHERE lease_id=?", (lease,))
            if not self._is_quota_error(error): return
            current = db.execute("SELECT penalty FROM quota_cooldowns WHERE project=? AND bucket=?", (self.project,bucket)).fetchone()
            penalty = min(4, (current["penalty"]+1) if current else 1); retry_after = None
            headers = getattr(getattr(error,"response",None),"headers",None)
            if headers:
                try: retry_after = float(headers.get("Retry-After"))
                except (TypeError,ValueError): pass
            delay = retry_after if retry_after is not None else min(60.0, 5.0 * 2**(penalty-1))
            db.execute("INSERT INTO quota_cooldowns VALUES(?,?,?,?) ON CONFLICT(project,bucket) DO UPDATE SET until_at=excluded.until_at,penalty=excluded.penalty", (self.project,bucket,now+delay,penalty))

    def status(self, identities):
        identities = tuple(identities); snapshot = self.refresh(identities); now = self.clock(); result=[]
        with self._db() as db:
            for identity in identities:
                p = snapshot.policies[identity.artifact_id]
                events = db.execute("SELECT * FROM quota_events WHERE project=? AND bucket=? AND started_at>?", (self.project,p.bucket,now-60)).fetchall()
                result.append({
                    "identity": identity.artifact_id, "model": identity.model, "bucket": p.bucket, "mode": p.mode,
                    "provider": {"requests_per_minute": p.provider_requests_per_minute, "input_tokens_per_minute": p.provider_input_tokens_per_minute, "output_tokens_per_minute": p.provider_output_tokens_per_minute},
                    "bba": {"requests_per_minute": p.effective_requests_per_minute, "input_tokens_per_minute": p.effective_input_tokens_per_minute, "output_tokens_per_minute": p.effective_output_tokens_per_minute, "minimum_spacing_seconds": p.minimum_spacing_seconds},
                    "rolling_usage": {"requests": len(events), "input_tokens": sum(int(r["actual_input"] if r["actual_input"] is not None else r["reserved_input"]) for r in events), "output_tokens": sum(int(r["actual_output"] if r["actual_output"] is not None else r["reserved_output"]) for r in events)},
                })
        return {"schema_version":1,"project":self.project,"location":self.location,"utilization":self.utilization,"discovered_at":snapshot.discovered_at,"models":result}
