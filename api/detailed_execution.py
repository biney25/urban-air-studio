"""Separate, fenced Phase B diagnostic queue; native execution never implies qualification.

This module intentionally does not share Phase A's result/run table. The existing
Store transaction authority serializes detailed job revisions on SQLite and
PostgreSQL. The worker is a separate process and executes only trusted scientific
code, with frozen compiler files; request bodies contain no commands or paths.
"""
from contextlib import closing
from copy import deepcopy
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Annotated, Literal
import zipfile

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from api.phase_b import CASE_KIND, checked_case_files, verify_record, allocation_dependencies
from api.responses import Output
from api.schemas import Identifier, Input, Name, Rights
from api.storage import Store, canonical, digest, new_id, utc_now

ROOT = Path(__file__).resolve().parents[1]
JOB_KIND = "phase_b_execution_job"
RECEIPT_KIND = "phase_b_execution_receipt"
USAGE_KIND = "phase_b_execution_usage"
PROTOCOL = "phase-b-diagnostic-worker-v1"
ACTIVE = {"queued", "preparing", "running", "verifying"}
TERMINAL = {"diagnostic_only", "complete_unqualified", "failed", "cancelled"}
TRANSIENT = {"RUNTIME_UNAVAILABLE", "RUNTIME_CHANGED", "WORKER_LOST", "ARTIFACT_STORAGE_FAILED"}
MAX_JOBS = 100
MAX_ACTIVE_PER_TENANT = 2
MAX_ARTIFACT_BYTES = 32_000_000
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_ATTEMPTS = 3
LEASE_SECONDS = 30
ID_RE = re.compile(r"dexec_[0-9a-f]{32}")
SHA_RE = re.compile(r"sha256:[0-9a-f]{64}")


class ExecutionRequest(Input):
    case_id: Identifier
    expected_case_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
    title: Name = "Detailed diagnostic execution"
    mode: Literal["research_diagnostic"] = "research_diagnostic"


class ExecutionJob(Output):
    schema_version: Literal["phase-b-execution-job-v1"]
    id: str
    project_id: str
    case_id: str
    title: str
    status: Literal["queued", "preparing", "running", "verifying", "diagnostic_only", "complete_unqualified", "failed", "cancelled"]
    qualified: Literal[False]
    mode: Literal["research_diagnostic"]
    numerical_verification_passed: bool
    created_at: str
    attempt: int
    cancel_requested: bool
    frozen: dict
    resource_use: dict
    error: dict | None
    receipt_id: str | None
    content_hash: str


class ExecutionReadiness(Output):
    schema_version: Literal["phase-b-execution-readiness-v1"]
    project_id: str
    available: bool
    qualified: Literal[False]
    mode: Literal["research_diagnostic"]
    runtime: dict
    admission: dict


def _hash_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _logical(record):
    return {key: value for key, value in record.items() if key != "content_hash"}


def _seal(record):
    record["content_hash"] = digest(_logical(record))
    return record


def _check_job(record, *, identifier=None, tenant=None, project=None):
    if (not isinstance(record, dict) or record.get("content_hash") != digest(_logical(record))
            or not ID_RE.fullmatch(record.get("id", ""))):
        raise ValueError("Detailed job integrity changed")
    if ((identifier is not None and record["id"] != identifier)
            or (tenant is not None and record.get("_tenant") != tenant)
            or (project is not None and record.get("project_id") != project)):
        raise ValueError("Detailed job differs from its authoritative object row/scope")
    ExecutionJob.model_validate(_public_job(record))
    if (record["attempt"] not in range(1, MAX_ATTEMPTS + 1)
            or not isinstance(record.get("_fence"), int) or isinstance(record["_fence"], bool)
            or record["_fence"] < 0 or record["qualified"] is not False
            or record["frozen"].get("protocol") != PROTOCOL):
        raise ValueError("Detailed worker protocol/attempt integrity changed")
    if record["status"] == "complete_unqualified" and record["numerical_verification_passed"] is not True:
        raise ValueError("A numerical failure cannot become completed research output")
    return record


def _public_job(record):
    result = {key: value for key, value in record.items() if not key.startswith("_")}
    # The public hash identifies the full sealed state; private worker lease,
    # object pointer and tenant fields are never published as editable input.
    return result


def _store_job(db, tenant, record):
    _seal(record)
    _check_job(record, tenant=tenant)
    result = db.execute("UPDATE objects SET data=? WHERE kind=? AND id=? AND tenant_id=? AND project_id=?",
                        (canonical(record), JOB_KIND, record["id"], tenant, record["project_id"]))
    if result.rowcount != 1:
        raise ValueError("Detailed job scope changed during mutation")


def _scoped(store, kind, identifier, project, identity, required, error, db=None):
    required(store, "project", project, identity, db)
    value = store.get(kind, identifier, identity.tenant_id, db)
    if value is None or value.get("project_id") != project:
        error("NOT_FOUND", "Resource not found in this project/tenant", 404)
    try:
        if kind == JOB_KIND:
            _check_job(value, identifier=identifier, tenant=identity.tenant_id, project=project)
        elif kind == CASE_KIND:
            verify_record(value, error, kind=CASE_KIND, identifier=identifier, project=project)
        elif (value.get("content_hash") != digest(_logical(value)) or value.get("id") != identifier
              or value.get("schema_version") != "phase-b-execution-receipt-v1"):
            raise ValueError("Immutable execution receipt changed")
    except (ValueError, TypeError, KeyError, ValidationError):
        error("ARTIFACT_INTEGRITY_ERROR", "Detailed artifact failed its stored identity/checksum", 503)
    return value


def _case_bundle(store, record, physical_hash):
    bundle = store.read_artifact(record["_artifact"], record["bundle_checksum"])
    checksums, size = checked_case_files(bundle["files"])
    binding = record["manifest"]["api_binding"]
    if (bundle["case_id"] != record["id"] or bundle["project_id"] != record["project_id"]
            or bundle["manifest"] != record["manifest"] or bundle["scenario_hash"] != record["scenario_hash"]
            or physical_hash(bundle["scenario"]) != record["scenario_hash"]
            or checksums != binding["file_checksums"] or digest(bundle["config"]) != binding["config_hash"]
            or size != record["file_bytes"] or len(checksums) != record["file_count"]
            or bundle["scenario"].get("source_rights") != record["rights"]):
        raise ValueError("Prepared execution dependencies changed")
    if bundle["scenario"].get("source_allocation") != binding.get("source_allocation"):
        raise ValueError("Prepared inventory allocation authority changed")
    # Stored object hashes alone are insufficient: malicious dictionaries can
    # enable coded objects or external libraries. Regenerate from the trusted
    # compiler and demand exact controlled files plus the method manifest.
    from science.phase_b import build_case
    regenerated = build_case(deepcopy(bundle["scenario"]), deepcopy(bundle["config"]))
    original_manifest = deepcopy(bundle["manifest"])
    original_manifest.pop("api_binding", None)
    if regenerated["files"] != bundle["files"] or regenerated["manifest"] != original_manifest:
        raise ValueError("Stored case differs from trusted compiler regeneration")
    return bundle


def _permit(check, identity, project, record, action, rights):
    check.require(identity, project, action=action, identifier=record["id"], tenant_id=identity.tenant_id,
                  resource_project_id=record["project_id"], rights=rights)


def _runtime_identity(status):
    """Identity is attested by trusted native readiness, never a request body."""
    if not isinstance(status, dict) or status.get("available") is not True:
        raise ValueError("Native detailed runtime is unavailable")
    identity = status.get("runtime_identity")
    if not isinstance(identity, dict) or not identity or not SHA_RE.fullmatch(status.get("runtime_digest", "")):
        raise ValueError("Native runtime lacks verified provenance")
    if status["runtime_digest"] != digest(identity):
        raise ValueError("Native runtime digest does not identify the verified runtime")
    return {"runtime_identity": deepcopy(identity), "runtime_digest": status["runtime_digest"]}


def _windows_to_linux(path):
    resolved = Path(path).resolve()
    if not re.fullmatch(r"[A-Za-z]:", resolved.drive) or "\\" not in str(resolved):
        raise ValueError("Only local drive paths can enter the fixed WSL bridge")
    return "/mnt/" + resolved.drive[0].lower() + "/" + resolved.as_posix()[3:]


def _bridge_prefix():
    # These choices are operator configuration, not HTTP input. No shell is
    # involved and no uploaded filename can become an executable/argument flag.
    if os.name == "nt":
        return [str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "wsl.exe"),
                "-d", "Ubuntu-22.04", "-u", "urban-air", "--exec", "/usr/bin/python3",
                _windows_to_linux(ROOT / "science/phase_b/execution.py")]
    return [sys.executable, str(ROOT / "science/phase_b/execution.py")]


def native_runtime_readiness():
    receipt = os.getenv("UA_FOAM_RUNTIME_RECEIPT")
    if not receipt:
        return {"available": False, "qualified": False, "reason": "Pinned native runtime has not been configured"}
    try:
        # Receipt path is private operator state and never appears in public
        # failures. The scientific script verifies native files, not existence.
        command = _bridge_prefix() + ["readiness", "--runtime-receipt", receipt]
        process = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                                 shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if process.returncode != 0 or len(process.stdout) > 1_000_000:
            raise ValueError("Native readiness probe failed")
        status = json.loads(process.stdout)
        _runtime_identity(status)
        return status
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {"available": False, "qualified": False, "reason": "Native runtime verification failed closed"}


def native_execute(bundle, *, execution_id, fence, cancel_check):
    """Fixed bridge writes a controlled JSON request; Linux owns solver scratch."""
    if not ID_RE.fullmatch(execution_id) or isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
        raise ValueError("Invalid controlled execution identity")
    scratch = ROOT / ".runtime" / "detailed-bridge" / execution_id / str(fence)
    scratch.mkdir(parents=True, exist_ok=False)
    input_path, output_path, cancel_path = scratch / "input.json", scratch / "output.json", scratch / "cancel"
    input_path.write_text(canonical(bundle), encoding="utf-8")
    path_arg = _windows_to_linux if os.name == "nt" else lambda path: str(path.resolve())
    receipt = os.getenv("UA_FOAM_RUNTIME_RECEIPT")
    if not receipt:
        raise ValueError("Native runtime is unavailable")
    command = _bridge_prefix() + ["execute", "--runtime-receipt", receipt, "--input", path_arg(input_path),
        "--output", path_arg(output_path), "--execution-id", execution_id, "--fence", str(fence),
        "--workspace-root", "/home/urban-air/executions" if os.name == "nt" else str(ROOT / ".runtime/native-executions"),
        "--cancel-file", path_arg(cancel_path)]
    limit = bundle["config"]["max_wall_seconds"]
    start = time.monotonic()
    cancelled_at = None
    # Fixed bridge diagnostics stay local and bounded by the native script;
    # artifact publication uses its checked JSON, never stdout-as-attestation.
    with (scratch / "bridge.log").open("wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while process.poll() is None:
                if cancel_check() or time.monotonic() - start > limit + 10:
                    if cancelled_at is None:
                        cancel_path.write_text("cancel requested\n", encoding="utf-8")
                        cancelled_at = time.monotonic()
                    if time.monotonic() - cancelled_at > 15:
                        process.terminate()
                        raise TimeoutError("Trusted native worker did not acknowledge cancellation")
                if (scratch / "bridge.log").stat().st_size > MAX_LOG_BYTES:
                    cancel_path.write_text("log limit\n", encoding="utf-8")
                    process.terminate()
                    raise ValueError("Native bridge log exceeds its bound")
                time.sleep(0.2)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if not output_path.is_file() or output_path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("Native output unavailable or out of bounds")
    return json.loads(output_path.read_text(encoding="utf-8"))


def make_router(*, identity_dependency, store_dependency, required, project_resource, error, physical_hash,
                authorizer, readiness_fn=None):
    readiness_fn = readiness_fn or native_runtime_readiness
    router = APIRouter(tags=["Phase B native diagnostics"])

    def scoped(store, kind, identifier, project, identity, db=None):
        return _scoped(store, kind, identifier, project, identity, required, error, db)

    def case_authority(store, case, project, identity, check, action, db=None):
        scenario = project_resource(required(store, "scenario", case["scenario_id"], identity, db), project)
        try:
            rights = Rights.model_validate(scenario["source_rights"]).model_dump()
            if (scenario["version"] != case["scenario_version"] or physical_hash(scenario) != case["scenario_hash"]
                    or rights != case["rights"]):
                raise ValueError("Frozen scenario/source permission changed")
        except (ValueError, TypeError, KeyError, ValidationError):
            error("ARTIFACT_INTEGRITY_ERROR", "Frozen case source identity/permissions changed", 503)
        for value in (case, scenario):
            _permit(check, identity, project, value, action, rights)
        def require_allocation(record):
            source_rights=record.get("effective_rights",record.get("source_rights",record.get("rights")))
            _permit(check,identity,project,record,action,source_rights)
        allocation_dependencies(store,scenario,identity.tenant_id,error,config=case["manifest"]["config"],db=db,
                                require=require_allocation,current_method=action!="export")
        return scenario

    @router.get("/api/projects/{project_id}/detailed-executions/readiness", response_model=ExecutionReadiness)
    def readiness(project_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        runtime = readiness_fn()
        try:
            _runtime_identity(runtime)
            available = True
        except (ValueError, TypeError):
            available = False
        return {"schema_version": "phase-b-execution-readiness-v1", "project_id": project_id, "available": available,
                "qualified": False, "mode": "research_diagnostic", "runtime": runtime,
                "admission": {"qualified_tier_available": False, "caller_attestation_allowed": False,
                    "mesh_and_numerical_guards_required": True, "claims": "Executed diagnostics only; no environmental decision qualification",
                    "maximum_active_per_tenant": MAX_ACTIVE_PER_TENANT, "maximum_attempts": MAX_ATTEMPTS}}

    @router.post("/api/projects/{project_id}/detailed-executions", status_code=202, response_model=ExecutionJob)
    def submit(project_id: str, body: ExecutionRequest, response: Response,
               identity=Depends(identity_dependency), store=Depends(store_dependency)):
        case = scoped(store, CASE_KIND, body.case_id, project_id, identity)
        if body.expected_case_hash != case["content_hash"]:
            error("VERSION_CONFLICT", "Expected case hash differs from the selected immutable case", 409)
        check = authorizer.check()
        for action in ("derive", "solve"):
            case_authority(store, case, project_id, identity, check, action)
        try:
            bundle = _case_bundle(store, case, physical_hash)
        except (OSError, ValueError, KeyError, TypeError):
            error("ARTIFACT_INTEGRITY_ERROR", "Prepared case bytes/dependencies failed verification", 503)
        request_hash = digest(body.model_dump(mode="json"))
        runtime = readiness_fn()
        with store.transaction() as db:
            required(store, "project", project_id, identity, db)
            current = scoped(store, CASE_KIND, body.case_id, project_id, identity, db)
            if current["content_hash"] != case["content_hash"]:
                error("ARTIFACT_INTEGRITY_ERROR", "Frozen case changed before queue publication", 503)
            for action in ("derive", "solve"):
                case_authority(store, current, project_id, identity, check, action, db)
            rows = db.execute("SELECT id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                              (JOB_KIND, identity.tenant_id, project_id, MAX_JOBS + 1)).fetchall()
            jobs = [_check_job(json.loads(row["data"]), identifier=row["id"], tenant=identity.tenant_id, project=project_id) for row in rows]
            for old in jobs:
                if old["_idempotency_key"] == body.idempotency_key:
                    if old["_request_hash"] != request_hash:
                        error("IDEMPOTENCY_CONFLICT", "Submission key already binds a different detailed request", 409)
                    response.headers.update(check.headers())
                    return _public_job(old)
            try:
                runtime_binding = _runtime_identity(runtime)
            except (ValueError, TypeError):
                error("RUNTIME_UNAVAILABLE", "Pinned native detailed runtime is unavailable; no job was queued", 503)
            if len(jobs) >= MAX_JOBS:
                error("PROJECT_ARTIFACT_LIMIT", "This project has reached its 100 detailed execution allowance", 409)
            active_rows = db.execute("SELECT id,project_id,data FROM objects WHERE kind=? AND tenant_id=?", (JOB_KIND, identity.tenant_id)).fetchall()
            if sum(_check_job(json.loads(row["data"]), identifier=row["id"], tenant=identity.tenant_id,
                              project=row["project_id"])["status"] in ACTIVE for row in active_rows) >= MAX_ACTIVE_PER_TENANT:
                error("CONCURRENCY_LIMIT", "At most two detailed diagnostic jobs may be active for this workspace", 409)
            frozen = {"protocol": PROTOCOL, "case_content_hash": case["content_hash"], "case_bundle_checksum": case["bundle_checksum"],
                      "scenario_hash": case["scenario_hash"], "rights": deepcopy(case["rights"]),
                      "compiler_method": deepcopy(case["manifest"]["method"]), **runtime_binding,
                      "maximum_total_seconds": bundle["config"]["max_wall_seconds"]}
            job = {"schema_version": "phase-b-execution-job-v1", "id": new_id("dexec"), "project_id": project_id,
                "case_id": case["id"], "title": body.title, "status": "queued", "qualified": False,
                "mode": body.mode, "numerical_verification_passed": False, "created_at": utc_now(), "attempt": 1,
                "cancel_requested": False, "frozen": frozen, "resource_use": {"worker_seconds": 0, "billed_currency": None},
                "error": None, "receipt_id": None, "_fence": 0, "_lease_until": None, "_worker": None,
                "_idempotency_key": body.idempotency_key, "_request_hash": request_hash, "_tenant": identity.tenant_id}
            _seal(job)
            _check_job(job)
            Store.put(db, JOB_KIND, identity.tenant_id, job, project_id)
        response.headers.update(check.headers())
        return _public_job(job)

    @router.get("/api/projects/{project_id}/detailed-executions", response_model=list[ExecutionJob])
    def executions(project_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        try:
            with closing(store.connect()) as db:
                rows = db.execute("SELECT id,project_id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                                  (JOB_KIND, identity.tenant_id, project_id, MAX_JOBS + 1)).fetchall()
            if len(rows) > MAX_JOBS:
                raise ValueError("Detailed project limit exceeded")
            records = [_check_job(json.loads(row["data"]), identifier=row["id"], tenant=identity.tenant_id,
                                  project=row["project_id"]) for row in rows]
            return [_public_job(row) for row in sorted(records, key=lambda row: row["created_at"], reverse=True)]
        except (ValueError, TypeError, KeyError):
            error("ARTIFACT_INTEGRITY_ERROR", "Detailed execution history could not be verified", 503)

    @router.get("/api/projects/{project_id}/detailed-executions/{execution_id}", response_model=ExecutionJob)
    def get_execution(project_id: str, execution_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        return _public_job(scoped(store, JOB_KIND, execution_id, project_id, identity))

    @router.post("/api/projects/{project_id}/detailed-executions/{execution_id}/cancel", response_model=ExecutionJob)
    def cancel(project_id: str, execution_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            job = scoped(store, JOB_KIND, execution_id, project_id, identity, db)
            if job["status"] in TERMINAL:
                return _public_job(job)
            job["cancel_requested"] = True
            if job["status"] == "queued":
                job.update(status="cancelled", finished_at=utc_now())
            _store_job(db, identity.tenant_id, job)
        return _public_job(job)

    @router.post("/api/projects/{project_id}/detailed-executions/{execution_id}/retry", status_code=202, response_model=ExecutionJob)
    def retry(project_id: str, execution_id: str, response: Response,
              identity=Depends(identity_dependency), store=Depends(store_dependency)):
        check = authorizer.check()
        runtime = readiness_fn()
        with store.transaction() as db:
            job = scoped(store, JOB_KIND, execution_id, project_id, identity, db)
            case = scoped(store, CASE_KIND, job["case_id"], project_id, identity, db)
            for action in ("derive", "solve"):
                case_authority(store, case, project_id, identity, check, action, db)
            if job["status"] != "failed" or job.get("error", {}).get("code") not in TRANSIENT:
                error("RETRY_NOT_ALLOWED", "Only infrastructure failures may retry; diagnose numerical failures before preparing a new case", 409)
            if job["attempt"] >= MAX_ATTEMPTS or job["resource_use"]["worker_seconds"] >= job["frozen"]["maximum_total_seconds"]:
                error("ALLOWANCE_EXHAUSTED", "This frozen diagnostic job has exhausted its attempt/time allowance", 409)
            try:
                if _runtime_identity(runtime) != {key: job["frozen"][key] for key in ("runtime_identity", "runtime_digest")}:
                    error("RUNTIME_CHANGED", "Retry requires the original verified native runtime", 409)
            except (ValueError, TypeError):
                error("RUNTIME_UNAVAILABLE", "Pinned native runtime is unavailable", 503)
            active = db.execute("SELECT id,project_id,data FROM objects WHERE kind=? AND tenant_id=?", (JOB_KIND, identity.tenant_id)).fetchall()
            if sum(_check_job(json.loads(row["data"]), identifier=row["id"], tenant=identity.tenant_id,
                              project=row["project_id"])["status"] in ACTIVE for row in active) >= MAX_ACTIVE_PER_TENANT:
                error("CONCURRENCY_LIMIT", "Detailed worker concurrency allowance is full", 409)
            job.update(status="queued", attempt=job["attempt"] + 1, error=None, receipt_id=None, cancel_requested=False,
                       _lease_until=None, _worker=None, numerical_verification_passed=False)
            job.pop("finished_at", None)
            _store_job(db, identity.tenant_id, job)
        response.headers.update(check.headers())
        return _public_job(job)

    def checked_receipt(store, project, execution, identity, export=False):
        job = scoped(store, JOB_KIND, execution, project, identity)
        if not job["receipt_id"]:
            error("DIAGNOSTIC_UNAVAILABLE", "No committed diagnostic receipt exists for this attempt", 409)
        record = scoped(store, RECEIPT_KIND, job["receipt_id"], project, identity)
        if record["execution_id"] != execution or record["fence"] != job["_fence"]:
            error("ARTIFACT_INTEGRITY_ERROR", "Diagnostic receipt is not the current committed attempt", 503)
        check = authorizer.check()
        case = scoped(store, CASE_KIND, job["case_id"], project, identity)
        if export:
            case_authority(store, case, project, identity, check, "export")
            _permit(check, identity, project, record, "export", record["rights"])
        try:
            payload = store.read_artifact(record["_artifact"], record["artifact_checksum"])
            if (payload["receipt"] != {key: value for key, value in record.items() if key not in {"_artifact", "artifact_checksum", "content_hash"}}
                    or digest(payload["native_execution"]) != record["native_execution_hash"]):
                raise ValueError("Native receipt binding changed")
            _verify_native_output(payload["native_execution"], job)
        except (OSError, ValueError, TypeError, KeyError):
            error("ARTIFACT_INTEGRITY_ERROR", "Committed native diagnostic bytes failed verification", 503)
        return record, payload, check

    @router.get("/api/projects/{project_id}/detailed-executions/{execution_id}/receipt")
    def receipt(project_id: str, execution_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        record, payload, _ = checked_receipt(store, project_id, execution_id, identity)
        return {key: value for key, value in record.items() if not key.startswith("_")}

    @router.get("/api/projects/{project_id}/detailed-executions/{execution_id}/download", response_class=Response)
    def download(project_id: str, execution_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        record, payload, check = checked_receipt(store, project_id, execution_id, identity, export=True)
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as zipped:
            files = {"receipt.json": canonical({key: value for key, value in record.items() if not key.startswith("_")}),
                     "native-execution.json": canonical(payload["native_execution"])}
            for name, value in payload["native_execution"]["logs"].items():
                files["logs/" + name] = value
            for name, value in sorted(files.items()):
                info = zipfile.ZipInfo(execution_id + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zipped.writestr(info, value.encode("utf-8"), compresslevel=3)
        return Response(archive.getvalue(), media_type="application/zip", headers={
            "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{execution_id}-diagnostics.zip"', **check.headers()})

    return router


def claim(store, worker, now=None):
    now = time.time() if now is None else now
    with store.transaction() as db:
        rows = db.execute("SELECT id,tenant_id,project_id,data FROM objects WHERE kind=? ORDER BY id", (JOB_KIND,)).fetchall()
        for row in rows:
            job = _check_job(json.loads(row["data"]), identifier=row["id"], tenant=row["tenant_id"], project=row["project_id"])
            if job["status"] not in ACTIVE or (job["status"] != "queued" and (job["_lease_until"] or 0) > now):
                continue
            if job["cancel_requested"]:
                job.update(status="cancelled", finished_at=utc_now(), _lease_until=None)
                _store_job(db, row["tenant_id"], job)
                continue
            if job["status"] != "queued":
                # A dead worker's resource consumption is uncertain. Charge its
                # full reserved attempt conservatively, preventing unlimited
                # crash/reclaim cycles from receiving fresh hidden allowances.
                charged = job["resource_use"]["worker_seconds"] + job.get("_attempt_allowance", 0)
                job["resource_use"]["worker_seconds"] = charged
                job["resource_use"]["lost_worker_consumption_basis"] = "full_reserved_attempt_not_measured"
                if job["attempt"] >= MAX_ATTEMPTS or charged >= job["frozen"]["maximum_total_seconds"]:
                    job.update(status="failed", finished_at=utc_now(), error={"code": "WORKER_LOST", "message": "Worker lease expired; reserved time is exhausted"}, _lease_until=None)
                    _store_job(db, row["tenant_id"], job)
                    continue
                job["attempt"] += 1
            remaining = job["frozen"]["maximum_total_seconds"] - job["resource_use"]["worker_seconds"]
            if remaining <= 0:
                job.update(status="failed", finished_at=utc_now(), error={"code": "ALLOWANCE_EXHAUSTED", "message": "Diagnostic time allowance exhausted"})
                _store_job(db, row["tenant_id"], job)
                continue
            job.update(status="preparing", _fence=job["_fence"] + 1, _worker=worker, _lease_until=now + LEASE_SECONDS,
                       _attempt_allowance=remaining, error=None, started_at=utc_now())
            _store_job(db, row["tenant_id"], job)
            return job
    return None


def _worker_update(store, job, stage=None):
    with store.transaction() as db:
        current = store.get(JOB_KIND, job["id"], job["_tenant"], db)
        if current is None:
            return False
        _check_job(current, identifier=job["id"], tenant=job["_tenant"], project=job["project_id"])
        if current["_fence"] != job["_fence"] or current["status"] in TERMINAL or current["cancel_requested"]:
            return False
        if stage:
            current["status"] = stage
        current["_lease_until"] = time.time() + LEASE_SECONDS
        _store_job(db, job["_tenant"], current)
        return True


def _verify_native_output(output, job):
    if (not isinstance(output, dict) or output.get("schema_version") != "phase-b-native-execution-v1"
            or output.get("qualified") is not False or output.get("status") not in {"executed_unqualified", "failed", "cancelled"}
            or len(canonical(output).encode("utf-8")) > MAX_ARTIFACT_BYTES):
        raise ValueError("Native execution contract is unsupported or unbounded")
    attestation = output.get("attestation", {})
    if (attestation.get("execution_id") != job["id"] or attestation.get("fence") != job["_fence"]
            or attestation.get("case_bundle_checksum") != job["frozen"]["case_bundle_checksum"]
            or attestation.get("runtime_digest") != job["frozen"]["runtime_digest"]):
        raise ValueError("Native output does not bind the frozen attempt/runtime/case")
    logs = output.get("logs")
    if not isinstance(logs, dict) or len(logs) > 100:
        raise ValueError("Native execution logs exceed bounded support")
    count = 0
    for name, value in logs.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", name)
                or name in {".", ".."} or not isinstance(value, str) or "\0" in value):
            raise ValueError("Native log filenames/content cannot enter export")
        count += len(value.encode("utf-8"))
    if count > MAX_LOG_BYTES:
        raise ValueError("Native output logs exceed their allowance")
    numerical = output.get("diagnostics", {}).get("numerical_verification_passed")
    if numerical not in {True, False} or not isinstance(numerical, bool):
        raise ValueError("Native numerical verification must be an explicit boolean")
    # The executor must expose each required internal gate rather than allowing
    # a successful process exit to stand in for scientific numerical evidence.
    if numerical:
        gates = output["diagnostics"].get("numerical_gates")
        expected = {"mesh_quality", "fluid_connectivity", "source_assignment", "receptor_support",
                    "scalar_positivity", "scalar_mass_balance", "velocity_stationarity", "scalar_stationarity"}
        if not isinstance(gates, dict) or set(gates) != expected or any(value is not True for value in gates.values()):
            raise ValueError("Native successful output lacks required internal numerical gates")
    analysis = output.get("analysis")
    if analysis is not None:
        if (not isinstance(analysis, dict) or analysis.get("schema_version") != "phase-b-native-field-diagnostics-v1"
                or analysis.get("qualified") is not False or analysis.get("numerical_verification_passed") is not False
                or analysis.get("C_unit") != "kg/m3" or analysis.get("reported_C_unit") != "ug/m3"
                or analysis.get("ug_per_kg") != 1e9
                or analysis.get("temporal_support") != "stationary_solver_iteration_end_state_not_measured_interval_mean"):
            raise ValueError("Native field diagnostics have unsupported units, support or qualification")
    return output


def _finish(store, job, status, *, seconds, error=None, receipt=None, pointer=None, checksum=None, numerical=False,
            commit_check=None):
    with store.transaction() as db:
        current = store.get(JOB_KIND, job["id"], job["_tenant"], db)
        if current is None:
            return False
        _check_job(current, identifier=job["id"], tenant=job["_tenant"], project=job["project_id"])
        if current["_fence"] != job["_fence"] or current["status"] in TERMINAL:
            return False
        if current["cancel_requested"]:
            status, receipt, pointer, checksum, error, numerical = "cancelled", None, None, None, None, False
        if status == "complete_unqualified" and numerical is not True:
            raise ValueError("Failed numerical verification cannot be published as completion")
        if receipt is not None and commit_check is not None:
            # Source revocation/tampering must be checked in the same authority
            # transaction as publication, after the expensive solve finished.
            commit_check(db, current)
        event = {"id": job["id"] + "_" + str(job["_fence"]), "project_id": job["project_id"],
                 "execution_id": job["id"], "fence": job["_fence"], "seconds": round(seconds, 6), "created_at": utc_now(),
                 "basis": "worker_monotonic_seconds", "billed_currency": None}
        Store.put(db, USAGE_KIND, job["_tenant"], event, job["project_id"])
        if receipt:
            receipt = deepcopy(receipt)
            receipt.update(_artifact=pointer, artifact_checksum=checksum)
            _seal(receipt)
            Store.put(db, RECEIPT_KIND, job["_tenant"], receipt, job["project_id"])
        current.update(status=status, error=error, finished_at=utc_now(), _lease_until=None,
            receipt_id=receipt["id"] if receipt else None, numerical_verification_passed=numerical,
            resource_use={"worker_seconds": round(current["resource_use"]["worker_seconds"] + seconds, 6), "billed_currency": None})
        _store_job(db, job["_tenant"], current)
        return True


def work_once(store=None, worker_id=None, *, readiness_fn=None, execute_fn=None, before_publish=None):
    """Consume one durable claim. Injection seams serve meaningful failure tests."""
    from api.main import physical_hash
    from api.authorization import Authorizer
    from api.auth import Identity
    store = store or Store()
    if os.getenv("UA_MODE", "local") != "local" and store.adapter_name != "postgres-postgis-s3":
        raise RuntimeError("Public detailed workers require PostgreSQL/PostGIS and private S3")
    job = claim(store, worker_id or f"detailed-{os.getpid()}")
    if job is None:
        return False
    started = time.monotonic()
    readiness_fn, execute_fn = readiness_fn or native_runtime_readiness, execute_fn or native_execute
    last_heartbeat = 0

    def cancelled():
        nonlocal last_heartbeat
        if time.monotonic() - started >= job["_attempt_allowance"]:
            return True
        if time.monotonic() - last_heartbeat >= 2:
            last_heartbeat = time.monotonic()
            return not _worker_update(store, job)
        current = store.get(JOB_KIND, job["id"], job["_tenant"])
        return current is None or current["cancel_requested"] or current["_fence"] != job["_fence"]

    try:
        try:
            runtime = _runtime_identity(readiness_fn())
        except (ValueError, TypeError, OSError):
            _finish(store, job, "failed", seconds=time.monotonic() - started,
                    error={"code": "RUNTIME_UNAVAILABLE", "message": "Pinned native runtime became unavailable before execution"})
            return True
        if runtime != {key: job["frozen"][key] for key in ("runtime_identity", "runtime_digest")}:
            _finish(store, job, "failed", seconds=time.monotonic() - started,
                    error={"code": "RUNTIME_CHANGED", "message": "Native runtime differs from the frozen queued runtime"})
            return True
        case = store.get(CASE_KIND, job["case_id"], job["_tenant"])
        if not case or case["project_id"] != job["project_id"] or case["content_hash"] != job["frozen"]["case_content_hash"]:
            raise ValueError("Detailed case scope/hash changed")
        def invalid(*args):
            raise ValueError("Stored case is invalid")
        verify_record(case, invalid, kind=CASE_KIND, identifier=job["case_id"], project=job["project_id"])
        bundle = _case_bundle(store, case, physical_hash)
        if case["bundle_checksum"] != job["frozen"]["case_bundle_checksum"]:
            raise ValueError("Frozen case bundle changed")
        source = store.get("scenario", case["scenario_id"], job["_tenant"])
        if (not source or source.get("project_id") != job["project_id"] or physical_hash(source) != case["scenario_hash"]
                or source["source_rights"] != job["frozen"]["rights"]):
            raise ValueError("Frozen scientific source dependency changed")
        identity = Identity(job["_tenant"], "trusted-detailed-worker")
        check = Authorizer().check()
        def allocation_authority(source,db=None):
            def require_allocation(record):
                source_rights=record.get("effective_rights",record.get("source_rights",record.get("rights")))
                for purpose in ("derive","solve"):
                    _permit(check,identity,job["project_id"],record,purpose,source_rights)
            allocation_dependencies(store,source,job["_tenant"],invalid,config=bundle["config"],db=db,
                                    require=require_allocation,current_method=True)
        for value in (case, source):
            for action in ("derive", "solve"):
                _permit(check, identity, job["project_id"], value, action, job["frozen"]["rights"])
        allocation_authority(source)
        if cancelled() or not _worker_update(store, job, "running"):
            _finish(store, job, "cancelled", seconds=time.monotonic() - started)
            return True
        native = execute_fn(bundle, execution_id=job["id"], fence=job["_fence"], cancel_check=cancelled)
        _verify_native_output(native, job)
        if not _worker_update(store, job, "verifying"):
            _finish(store, job, "cancelled", seconds=time.monotonic() - started)
            return True
        numerical = native["diagnostics"]["numerical_verification_passed"]
        status = "failed" if native["status"] == "failed" else "cancelled" if native["status"] == "cancelled" else "complete_unqualified" if numerical else "diagnostic_only"
        public_diagnostics = deepcopy(native["diagnostics"])
        if native.get("analysis") is not None:
            # Raw iteration histories remain in the immutable native ZIP. The
            # ordinary receipt carries bounded scientific diagnostics only.
            public_diagnostics["field_analysis"] = {key: deepcopy(value) for key, value in native["analysis"].items()
                                                     if key != "probe_series"}
        receipt = {"schema_version": "phase-b-execution-receipt-v1", "id": new_id("dreceipt"), "project_id": job["project_id"],
            "execution_id": job["id"], "case_id": job["case_id"], "fence": job["_fence"], "attempt": job["attempt"],
            "created_at": utc_now(), "status": status, "qualified": False, "numerical_verification_passed": numerical,
            "rights": deepcopy(job["frozen"]["rights"]), "frozen": deepcopy(job["frozen"]), "native_execution_hash": digest(native),
            "diagnostics": public_diagnostics, "resources": deepcopy(native.get("resources", {})),
            "download_url": f"/api/projects/{job['project_id']}/detailed-executions/{job['id']}/download"}
        payload = {"receipt": receipt, "native_execution": native}
        try:
            pointer, checksum = store.atomic_artifact(job["id"], job["_fence"], payload)
            if checksum != digest(payload) or store.read_artifact(pointer, checksum) != payload:
                raise ValueError("Native diagnostic artifact differs from durable bytes")
        except (OSError, ValueError):
            _finish(store, job, "failed", seconds=time.monotonic() - started,
                    error={"code": "ARTIFACT_STORAGE_FAILED", "message": "Native diagnostic bytes were not durably published"})
            return True
        if before_publish:
            before_publish(job)
        def commit_authority(db, current):
            project = store.get("project", job["project_id"], job["_tenant"], db)
            committed_case = store.get(CASE_KIND, job["case_id"], job["_tenant"], db)
            committed_source = store.get("scenario", case["scenario_id"], job["_tenant"], db)
            if (project is None or committed_case is None or committed_source is None
                    or committed_case.get("project_id") != job["project_id"]
                    or committed_source.get("project_id") != job["project_id"]
                    or digest(committed_case) != digest(case) or digest(committed_source) != digest(source)):
                raise ValueError("Execution source authority changed before publication")
            for value in (committed_case, committed_source):
                for action in ("derive", "solve"):
                    _permit(check, identity, job["project_id"], value, action, job["frozen"]["rights"])
            allocation_authority(committed_source,db)
        _finish(store, job, status, seconds=time.monotonic() - started,
                error={"code": "NUMERICAL_DIAGNOSTIC_FAILED", "message": "Native scientific diagnostics failed; inspect retained receipt"} if status == "failed" else None,
                receipt=receipt, pointer=pointer, checksum=checksum, numerical=numerical, commit_check=commit_authority)
    except (OSError, subprocess.TimeoutExpired):
        _finish(store, job, "failed", seconds=time.monotonic() - started,
                error={"code": "RUNTIME_UNAVAILABLE", "message": "Native execution transport failed; no completion was published"})
    except Exception:
        _finish(store, job, "failed", seconds=time.monotonic() - started,
                error={"code": "EXECUTION_INTEGRITY_FAILED", "message": "Native execution input/output contract failed; no completion was published"})
    return True


def main():
    parser = argparse.ArgumentParser(description="Separate controlled Urban Air detailed diagnostic worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1)
    args = parser.parse_args()
    store = Store()
    while True:
        worked = work_once(store)
        if args.once:
            break
        if not worked:
            time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
