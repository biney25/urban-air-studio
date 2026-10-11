"""Isolated, ephemeral browser demo. Run ONLY as ``python -m infra.public_demo``.

This is a single-process free-host demonstration, not the Cognito/PostGIS/S3
deployment. No development identity, stored projects, native executor or local
model is made public. Existing API permission and scientific checks stay intact.
"""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

COOKIE = "__Host-uas-demo"
CSP = "; ".join(["default-src 'self'", "script-src 'self' 'wasm-unsafe-eval'",
    "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
    "font-src 'self' data:", "connect-src 'self' https://tiles.openfreemap.org",
    "worker-src 'self' blob:", "object-src 'none'", "base-uri 'self'",
    "frame-ancestors 'none'"])
BANNER = """<style>#uas-public-demo{position:relative;z-index:10000;padding:8px 16px;background:#fff1c4;color:#342900;font:13px/1.4 system-ui,sans-serif;border-bottom:1px solid #d6b253}#uas-public-demo strong{font-weight:700}</style><aside id="uas-public-demo" aria-label="Public demo notice"><strong>Public research demo</strong> · Temporary browser-only projects; resets erase access. T1 previews are unvalidated. Native CFD and local AI are not hosted. Do not upload private data.</aside>"""


@dataclass(frozen=True)
class Limits:
    sessions: int = 1000
    session_seconds: int = 86400
    requests_per_minute: int = 120
    writes_per_minute: int = 20
    global_requests_per_minute: int = 600
    body_bytes: int = 1_000_000
    objects_per_session: int = 250
    global_objects: int = 4000
    projects_per_session: int = 8
    runs_per_session: int = 12
    global_runs: int = 100
    active_per_session: int = 2
    global_active: int = 4
    storage_bytes: int = 128_000_000
    artifact_bytes: int = 4_000_000
    cells: int = 60_000
    cases_per_quote: int = 2
    duration_seconds: int = 120


def problem(code, message, status=429):
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


def origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("UA_DEMO_ORIGIN or RENDER_EXTERNAL_URL must be an HTTPS origin")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or 443


def size(directory):
    return sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())


def create_app(directory=None, dist=None, *, demo_origin=None, limits=None, start_worker=True):
    """Construct a demo without modifying UA_MODE or the ordinary application."""
    from api.auth import Identity
    from api.main import create_app as api_app, local_identity, get_store, required
    from api.storage import Store, canonical

    limits = limits or Limits()
    allowed_origin = origin(demo_origin or os.getenv("UA_DEMO_ORIGIN") or os.getenv("RENDER_EXTERNAL_URL", ""))
    directory = Path(directory or os.getenv("UA_DEMO_DATA_DIR", "/tmp/uas-public-demo")).resolve()
    root = Path(__file__).resolve().parents[1]
    if directory == root or directory.is_relative_to(root / "sources") or directory.is_relative_to(root / "runtime"):
        raise ValueError("Public demo requires a dedicated directory, never existing project/runtime data")
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".public-demo-only"
    if not marker.exists() and any(directory.iterdir()):
        raise ValueError("Refusing to serve a non-empty directory without the public-demo marker")
    marker.write_text("Ephemeral public demo data only. No accounts.\n", encoding="utf-8")
    dist = Path(dist or root / "web" / "dist").resolve()

    class DemoStore(Store):
        def connect(self):
            db = super().connect()
            # A second hard bound on SQLite growth; WAL is also checked at admission.
            db.execute("PRAGMA max_page_count=32768")
            return db

        def atomic_artifact(self, run_id, fence, payload):
            encoded = canonical(payload).encode("utf-8")
            if len(encoded) > limits.artifact_bytes or size(directory) + len(encoded) > limits.storage_bytes:
                raise OSError("Public demo artifact storage allowance reached")
            return super().atomic_artifact(run_id, fence, payload)

    database_url = os.getenv("UA_DATABASE_URL") or os.getenv("DATABASE_URL")
    if database_url and database_url.startswith(("postgresql://", "postgres://")):
        try:
            from api.postgres import PostgresStore
            class DemoPostgresStore(PostgresStore):
                def atomic_artifact(self, run_id, fence, payload):
                    encoded = canonical(payload).encode("utf-8")
                    if len(encoded) > limits.artifact_bytes or size(directory) + len(encoded) > limits.storage_bytes:
                        raise OSError("Public demo artifact storage allowance reached")
                    return super().atomic_artifact(run_id, fence, payload)
            store = DemoPostgresStore(directory, database_url=database_url, auto_migrate=True)
        except Exception as exc:
            logging.getLogger(__name__).warning("DemoPostgresStore fallback: %s", exc)
            store = DemoStore(directory)
    else:
        store = DemoStore(directory)
    app = api_app(directory)
    app.state.store = store
    app.state.demo_limits = limits
    app.dependency_overrides[get_store] = lambda: store

    def demo_identity(request: Request):
        if request.headers.get("authorization"):
            raise HTTPException(403, detail={"code": "REVIEW_SCOPE_DENIED", "message": "A review credential is not a demo planner session"})
        sid = getattr(request.state, "demo_session", None)
        if not sid:
            raise HTTPException(401, detail={"code": "DEMO_SESSION_REQUIRED", "message": "Open the demo in this browser first"})
        return Identity("demo:" + sid, "demo:" + sid)

    app.dependency_overrides[local_identity] = demo_identity
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app):
        thread = None
        if start_worker:
            if os.getenv("UA_MODE", "local") != "local":
                raise RuntimeError("Start this isolated demo through python -m infra.public_demo")
            from api.worker import work_once
            def run():
                while not stop.is_set():
                    try:
                        worked = work_once(store, worker_id="public-demo-t1")
                    except Exception:
                        logging.getLogger(__name__).exception("Demo T1 worker failed")
                        worked = False
                    if not worked:
                        stop.wait(0.5)
            thread = threading.Thread(target=run, name="public-demo-t1", daemon=True)
            thread.start()
            app.state.demo_worker = thread
        yield
        stop.set()
        if thread:
            await asyncio.to_thread(thread.join, 3)

    app.router.lifespan_context = lifespan
    secret_file = directory / ".demo-secret"
    secret_env = os.getenv("UA_DEMO_SECRET") or os.getenv("SECRET_KEY")
    if secret_env:
        secret = hashlib.sha256(secret_env.encode("utf-8")).digest()
    elif secret_file.exists():
        try:
            secret = secret_file.read_bytes()
        except OSError:
            secret = secrets.token_bytes(32)
    else:
        secret = secrets.token_bytes(32)
        try:
            secret_file.write_bytes(secret)
        except OSError:
            pass
    sessions = {}
    global_requests = deque()
    write_lock = asyncio.Lock()  # Admission plus mutation is atomic in one process.

    def signed(sid, expires):
        value = f"{sid}.{expires}"
        return value + "." + hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()

    def session(request):
        now = time.time()
        for sid in list(sessions):
            if sessions[sid]["expires"] <= now:
                del sessions[sid]
        token = request.cookies.get(COOKIE)
        if token:
            if not re.fullmatch(r"[0-9a-f]{48}\.[0-9]{10}\.[0-9a-f]{64}", token):
                return None, False, problem("DEMO_SESSION_INVALID", "Demo cookie is invalid; clear it and reopen the demo", 401)
            sid, expires, _ = token.split(".")
            if not hmac.compare_digest(token, signed(sid, expires)) or int(expires) <= now:
                return None, False, problem("DEMO_SESSION_INVALID", "Demo session expired; clear the cookie and reopen the demo", 401)
            if sid not in sessions:
                sessions[sid] = {"expires": int(expires), "requests": deque(), "writes": deque()}
            return sid, False, None
        if len(sessions) >= limits.sessions:
            return None, False, problem("DEMO_SESSION_CAP", "The public demo session allowance is full; try after the next reset")
        sid = secrets.token_hex(24)
        sessions[sid] = {"expires": int(now) + limits.session_seconds, "requests": deque(), "writes": deque()}
        return sid, True, None

    def rate(queue, maximum, now):
        while queue and queue[0] <= now - 60:
            queue.popleft()
        if len(queue) >= maximum:
            return False
        queue.append(now)
        return True

    def capacity(path, sid, body):
        tenant = "demo:" + sid
        submitted = re.fullmatch(r"/api/projects/([^/]+)/runs", path)
        if submitted and isinstance(body.get("idempotency_key"), str):
            with closing(store.connect()) as db:
                if db.execute("SELECT 1 FROM submissions WHERE tenant_id=? AND project_id=? AND key=?",
                              (tenant, submitted.group(1), body["idempotency_key"])).fetchone():
                    # The API checks the quote hash and returns existing IDs (or a
                    # conflict). Recovering a submission consumes no new capacity.
                    return None
        if size(directory) >= limits.storage_bytes - limits.artifact_bytes:
            return problem("DEMO_STORAGE_CAP", "Temporary demo storage is full; try after the next reset")
        with closing(store.connect()) as db:
            objects = db.execute("SELECT COUNT(*) FROM objects WHERE tenant_id=?", (tenant,)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
            # Reserve room for multi-record operations and a two-run submission.
            if objects + 20 > limits.objects_per_session or total + 20 > limits.global_objects:
                return problem("DEMO_OBJECT_CAP", "The public demo project/object allowance has been reached")
            if path == "/api/projects":
                count = db.execute("SELECT COUNT(*) FROM objects WHERE tenant_id=? AND kind='project'", (tenant,)).fetchone()[0]
                if count >= limits.projects_per_session:
                    return problem("DEMO_PROJECT_CAP", "This browser has reached its temporary project allowance")
            if re.fullmatch(r"/api/projects/[^/]+/runs", path) or re.fullmatch(r"/api/runs/[^/]+/retry", path):
                cases = 1
                if path.endswith("/runs"):
                    quote = store.get("quote", body.get("quote_id", ""), tenant, db)
                    if quote:
                        cases = quote["cases"]
                own, total = db.execute("SELECT COUNT(*) FROM runs WHERE tenant_id=?", (tenant,)).fetchone()[0], db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
                active, global_active = db.execute("SELECT COUNT(*) FROM runs WHERE tenant_id=? AND status IN ('queued','preparing','solving','verifying')", (tenant,)).fetchone()[0], db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','preparing','solving','verifying')").fetchone()[0]
                if own + cases > limits.runs_per_session or total + cases > limits.global_runs or active + cases > limits.active_per_session or global_active + cases > limits.global_active:
                    return problem("DEMO_RUN_CAP", "The bounded public T1 run allowance is full; finish current jobs or wait for a reset")
            if path.endswith("/quote"):
                identifiers = body.get("scenario_ids", [])
                if not isinstance(identifiers, list) or len(identifiers) > limits.cases_per_quote:
                    return problem("DEMO_CASE_CAP", "The public demo accepts at most two scenarios per quote", 422)
                conditions = body.get("conditions", {})
                if isinstance(conditions, dict) and isinstance(conditions.get("duration_s"), (int, float)) and conditions["duration_s"] > limits.duration_seconds:
                    return problem("DEMO_DURATION_CAP", "Public T1 previews allow at most 120 simulated seconds", 422)
                for identifier in identifiers:
                    if not isinstance(identifier, str):
                        continue  # Ordinary API validation handles malformed inputs.
                    scenario = store.get("scenario", identifier, tenant, db)
                    if scenario:
                        domain = scenario.get("analysis_domain", {})
                        if domain:
                            cells = math.ceil((domain["xmax"] - domain["xmin"]) / domain.get("cell_size_m", 8)) * math.ceil((domain["ymax"] - domain["ymin"]) / domain.get("cell_size_m", 8)) * math.ceil(domain["zmax"] / domain.get("vertical_cell_size_m", 4))
                            if cells > limits.cells:
                                return problem("DEMO_GRID_CAP", "This grid exceeds the free demo cell allowance; geometry is never silently coarsened", 422)
        return None

    @app.middleware("http")
    async def guard(request, call_next):
        path = request.url.path
        mutating = request.method not in {"GET", "HEAD", "OPTIONS"}
        now = time.monotonic()
        response = None
        sid, fresh = None, False
        if not rate(global_requests, limits.global_requests_per_minute, now):
            response = problem("DEMO_RATE_LIMIT", "Public demo request allowance reached; retry in a minute")
        if response is None and mutating:
            try:
                same = origin(request.headers.get("origin", "")) == allowed_origin
            except ValueError:
                same = False
            if not same or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
                response = problem("DEMO_ORIGIN_DENIED", "Writes require the configured demo browser origin", 403)
        authorization = request.headers.get("authorization", "")
        if response is None and authorization and path not in {"/api/review/report", "/api/review/report/comments", "/api/review/report/download"}:
            response = problem("REVIEW_SCOPE_DENIED" if "uarv1_" in authorization.lower() else "DEMO_AUTHORIZATION_DENIED", "A bearer credential is not an anonymous demo planner session", 403)
        public = path in {"/api/health", "/api/ready"}
        if response is None and not public:
            sid, fresh, response = session(request)
            if sid:
                request.state.demo_session = sid
                if not rate(sessions[sid]["requests"], limits.requests_per_minute, now) or (mutating and not rate(sessions[sid]["writes"], limits.writes_per_minute, now)):
                    response = problem("DEMO_RATE_LIMIT", "This browser's demo allowance is full; retry in a minute")
        if response is None and mutating and ("/detailed-executions" in path or (not os.getenv("GEMINI_API_KEY") and "/assistant/" in path)):
            response = problem("DEMO_RUNTIME_NOT_HOSTED", "Native CFD solver execution and local AI inference are not hosted on this free T1 demo", 503)
        if response is None:
            if mutating:
                # Bound the actual body here, before the API's larger local limit.
                count, chunks = 0, []
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > limits.body_bytes:
                        response = problem("DEMO_BODY_CAP", "Public demo requests must fit within 1 MB", 413)
                        break
                    chunks.append(chunk)
                if response is None:
                    request._body = b"".join(chunks)
                    try:
                        body = json.loads(request._body) if request._body else {}
                    except (ValueError, UnicodeError):
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                    async with write_lock:
                        response = capacity(path, sid, body)
                        if response is None:
                            response = await call_next(request)
            else:
                response = await call_next(request)
        if fresh:
            response.set_cookie(COOKIE, signed(sid, sessions[sid]["expires"]), max_age=limits.session_seconds,
                                secure=True, httponly=True, samesite="lax", path="/")
        elif response.status_code == 401 and sid is None:
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Strict-Transport-Security": "max-age=31536000", "X-Frame-Options": "DENY"})
        if not path.endswith("/download"):
            response.headers["Content-Security-Policy"] = CSP
        if response.status_code == 429:
            response.headers["Retry-After"] = "60"
        return response

    # Replace deployment metadata only; preserve every science and permission route.
    app.router.routes = [route for route in app.router.routes if getattr(route, "path", "") not in {
        "/api/auth/config", "/api/health", "/api/ready", "/api/capabilities",
        "/api/projects/{project_id}/assistant/status", "/api/projects/{project_id}/detailed-executions/readiness"}]

    @app.get("/api/auth/config")
    def config():
        return {"mode": "local", "configured": True, "demo": True, "identity": "anonymous_browser_session", "accounts": False}

    @app.get("/api/capabilities")
    def capabilities(identity=Depends(demo_identity)):
        from science import MODEL_VERSION
        gemini_ready = bool(os.getenv("GEMINI_API_KEY"))
        storage_kind = getattr(store, "adapter_name", "ephemeral_demo_sqlite")
        return {"phase": "Isolated public research demo", "model_version": MODEL_VERSION,
            "supported_tiers": ["T0", "T1"], "capability_statuses": {"T0": "implemented_geometry_diagnostics", "T1": "research_preview_unvalidated", "T2": "not_hosted_on_free_demo"},
            "species": ["passive_tracer"], "imports": ["GeoJSON Polygon/Point"], "identity": "anonymous_browser_session",
            "storage": storage_kind, "operator_views_available": False, "ai_available": gemini_ready,
            "study_assistant": {"state": "ready" if gemini_ready else "unavailable", "scope": "reviewed study tools via Gemini" if gemini_ready else "not hosted on free demo", "qualified": False},
            "cloud_deployed": False, "validated": False,
            "geographic_context": {"map_navigation": "Supported worldwide coordinates", "providers": ["OSM geometry", "Open-Meteo GFS weather context"], "semantic_preparation": "review_required", "national_scientific_coverage": False},
            "limitations": ["Ephemeral anonymous demo; no accounts or production cloud assurance", "T1 research previews are scientifically unvalidated", "Native CFD is not hosted; case download available"]}

    @app.get("/api/projects/{project_id}/assistant/status")
    def assistant_status(project_id: str, identity=Depends(demo_identity)):
        required(store, "project", project_id, identity)
        if os.getenv("GEMINI_API_KEY"):
            from api.assistant import LocalModel
            return LocalModel().status()
        return {"available": False, "state": "unavailable", "label": "Study assistant",
            "model": {"provider": "ollama", "model_id": "qwen3:1.7b"}, "limits": {},
            "reason_code": "DEMO_RUNTIME_NOT_HOSTED", "message": "Local AI is not hosted on this free demo; ordinary study tools remain available", "qualified": False, "evaluation": None}

    @app.get("/api/projects/{project_id}/detailed-executions/readiness")
    def native_status(project_id: str, identity=Depends(demo_identity)):
        required(store, "project", project_id, identity)
        return {"schema_version": "phase-b-execution-readiness-v1", "project_id": project_id,
            "available": False, "qualified": False, "mode": "research_diagnostic",
            "runtime": {"available": False, "reason_code": "DEMO_RUNTIME_NOT_HOSTED", "reason": "Native CFD is not hosted on this free demo", "message": "Native CFD is not hosted on this free demo"},
            "admission": {"qualified_tier_available": False, "caller_attestation_allowed": False, "maximum_active_per_tenant": 0}}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "public_ephemeral_demo", "production_ready": False, "accounts": False}

    @app.get("/api/ready")
    def ready():
        with closing(store.connect()) as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ready", "mode": "public_ephemeral_demo", "scientific_qualification": "research_preview", "cloud_operation_accepted": False}

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            return problem("NOT_FOUND", "API route does not exist", 404)
        candidate = (dist / path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file() and candidate.name != "index.html":
            return FileResponse(candidate)
        index = dist / "index.html"
        if not index.is_file():
            return problem("DEMO_WEB_NOT_BUILT", "The demo website has not been built", 503)
        text = index.read_text(encoding="utf-8")
        return HTMLResponse(text.replace("<body>", "<body>" + BANNER, 1))

    # Newer FastAPI keeps included routers as nested routes. Put the explicit
    # deployment status routes before those routers, without touching their API.
    priority = {"/api/projects/{project_id}/assistant/status", "/api/projects/{project_id}/detailed-executions/readiness"}
    app.router.routes.sort(key=lambda route: 0 if getattr(route, "path", "") in priority else 1)
    return app


def main():
    # These settings apply exclusively to this standalone deployment process.
    os.environ["UA_MODE"] = "local"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8000")), workers=1)


if __name__ == "__main__":
    main()
