"""Explicit finite point-source rate studies; no inference, run submission or ranking."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator

from api.planning import latest_brief
from api.preview_scope import inventory_bound, CODE, MESSAGE
from api.schemas import Input, Number, Name, Identifier, ComparisonRequest, OperationsRequest
from api.storage import digest, new_id, utc_now
from science.geometry import validate_scenario, ScienceError

Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ResponseStudyPreviewRequest(Input):
    name: Name
    scenario_id: Identifier
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    parent_hash: Hash
    brief_hash: Hash
    source_ids: Annotated[list[Identifier], Field(min_length=1, max_length=30)]
    factors: Annotated[list[Annotated[Number, Field(ge=0, le=2)]], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def unique_selection(self):
        if len(set(self.source_ids)) != len(self.source_ids) or len(set(self.factors)) != len(self.factors):
            raise ValueError("Source IDs and factors must each be unique")
        return self


class ResponseStudyCommitRequest(Input):
    preview_id: Identifier
    preview_hash: Hash
    idempotency_key: Annotated[str, Field(min_length=8, max_length=120)]


class ResponseStudyCompareRequest(Input):
    candidate_scenario_id: Identifier
    baseline_run_id: Identifier
    alternative_run_id: Identifier


PROVENANCE = {
    "method": "deterministic_point_source_rate_grid_v1", "llm_used": False,
    "response_surrogate": False, "linearity_qualified": False, "optimal": False,
    "qualified": False, "runs_submitted": False,
    "limitations": ["Explicit candidate preparation only; source rate is an input, not a pollution benefit.",
                    "No response surrogate, physical prediction, Pareto frontier or proven optimum.",
                    "Run each candidate through the separate quoted and confirmed study workflow.",
                    "Completed comparisons remain unqualified research previews."],
}


def error(code, message, status=400, issues=None):
    detail = {"code": code, "message": message}
    if issues is not None:
        detail["issues"] = issues
    raise HTTPException(status_code=status, detail=detail)


def seal(record):
    record["content_hash"] = digest({k: v for k, v in record.items() if k != "content_hash"})
    return record


def checked(record):
    if record.get("content_hash") != digest({k: v for k, v in record.items() if k != "content_hash"}):
        error("RESPONSE_STUDY_INTEGRITY_ERROR", "Stored response study failed integrity checks", 503)
    return record


def records(db, kind, identity, project_id):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT data FROM objects WHERE kind=? AND tenant_id=? AND project_id=?",
        (kind, identity.tenant_id, project_id)).fetchall()]


def make_router(*, identity_dependency, store_dependency, required, project_resource,
                physical_hash, scenario_version, authorizer, constraint_validator, compare):
    router = APIRouter()
    prefix = "/api/projects/{project_id}/response-studies"

    def scoped(store, kind, identifier, identity, project_id, db=None):
        return project_resource(required(store, kind, identifier, identity, db), project_id)

    def permission(store, scenario, identity, gate, db=None):
        if scenario.get("source_preparation"):
            from api.site_preparation import checked_preparation_dependency
            checked_preparation_dependency(store, scenario, identity.tenant_id, error, db)
        gate.require(identity, scenario["project_id"], action="derive", identifier=scenario["id"],
                     tenant_id=identity.tenant_id, resource_project_id=scenario["project_id"],
                     rights=scenario.get("source_rights", {}))

    def parent_and_brief(store, project_id, request, identity, gate, db=None):
        required(store, "project", project_id, identity, db)
        parent = scoped(store, "scenario", request.scenario_id, identity, project_id, db)
        if parent.get("content_hash") != physical_hash(parent):
            error("SCENARIO_INTEGRITY_ERROR", "Stored parent scenario failed integrity checks", 503)
        if parent["version"] != request.expected_version or parent["content_hash"] != request.parent_hash:
            error("RESPONSE_STUDY_STALE", "Review the exact parent version before preparing candidates", 409)
        if inventory_bound(parent):
            error(CODE, MESSAGE, 409)
        permission(store, parent, identity, gate, db)
        brief = latest_brief(store, project_id, identity, db)
        if not brief:
            error("CONSTRAINT_REVIEW_REQUIRED", "Declare planning requirements before preparing this study", 409)
        if brief["content_hash"] != request.brief_hash:
            error("RESPONSE_STUDY_BRIEF_CHANGED", "Planning requirements changed; review a new preview", 409)
        by_id = {s["id"]: s for s in parent["sources"]}
        if any(identifier not in by_id for identifier in request.source_ids):
            error("RESPONSE_STUDY_SOURCE_SCOPE", "Select point sources belonging to the exact parent scenario", 422)
        for identifier in request.source_ids:
            source = by_id[identifier]
            if any(source.get(key) not in (None, "point", "Point", "source")
                   for key in ("type", "source_kind", "geometry_type")):
                error("RESPONSE_STUDY_SOURCE_KIND", "Only explicit point sources are eligible", 422)
        return parent, brief

    def candidate(store, project_id, request, parent, brief, identity, factor):
        core = deepcopy(parent)
        selected = set(request.source_ids)
        rates, operations = [], []
        for source in core["sources"]:
            if source["id"] in selected:
                before = source["rate_g_s"]
                source["rate_g_s"] = before * factor
                rates.append({"source_id": source["id"], "before_g_s": before, "after_g_s": source["rate_g_s"]})
                operations.append({"type": "source_update", "object_id": source["id"], "changes": {"rate_g_s": source["rate_g_s"]}})
        try:
            OperationsRequest(expected_version=parent["version"], operations=operations)
            diagnostics = validate_scenario(core)
            core["diagnostics"] = diagnostics
            validation = constraint_validator(project_id, parent, core, identity, store)
            if validation.get("brief_id") != brief["id"] or validation.get("brief_hash") != brief["content_hash"]:
                error("RESPONSE_STUDY_BRIEF_CHANGED", "Candidate requirements no longer match the reviewed brief", 409)
        except ScienceError as exc:
            validation = {"valid": False, "status": "geometry_failed", "issues": [{"code": exc.code, "message": exc.message}],
                          "evaluations": [], "brief_id": brief["id"], "brief_hash": brief["content_hash"]}
        except ValueError:
            validation = {"valid": False, "status": "geometry_failed", "issues": [{"code": "SOURCE_RATE_OUT_OF_DOMAIN", "message": "Scaled rate must remain within 0–100 g/s"}],
                          "evaluations": [], "brief_id": brief["id"], "brief_hash": brief["content_hash"]}
        suffix = f" · source rate ×{factor:g}"
        result = {"factor": factor, "name": request.name[:120-len(suffix)] + suffix,
                  "source_rates": rates, "operations": operations, "validation": validation,
                  "scenario_hash": physical_hash(core), "unchanged_control": physical_hash(core) == parent["content_hash"]}
        return core, result

    @router.post(prefix + "/preview", status_code=201)
    def preview(project_id: str, body: ResponseStudyPreviewRequest,
                identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            gate = authorizer.check()
            parent, brief = parent_and_brief(store, project_id, body, identity, gate, db)
            request = body.model_dump()
            identifier = "response_preview_" + digest({"tenant": identity.tenant_id, "user": identity.user_id,
                                                         "project": project_id, "request": request}).split(":")[1][:40]
            existing = store.get("response_study_preview", identifier, identity.tenant_id, db)
            if existing:
                return checked(existing)
            if len(records(db, "response_study_preview", identity, project_id)) >= 100:
                error("RESPONSE_STUDY_QUOTA", "This project has reached its 100-preview allowance", 429)
            candidates = []
            for factor in body.factors:
                core, result = candidate(store, project_id, body, parent, brief, identity, factor)
                permission(store, core, identity, gate, db)
                candidates.append(result)
            record = seal({"id": identifier, "project_id": project_id, "created_by": identity.user_id,
                           "created_at": utc_now(), "name": body.name, "request": request,
                           "parent_scenario_id": parent["id"], "parent_hash": parent["content_hash"], "parent_version": parent["version"],
                           "brief_id": brief["id"], "brief_hash": brief["content_hash"], "candidates": candidates,
                           "valid": all(c["validation"].get("valid") is True for c in candidates),
                           "requires_confirmation": True, "qualified": False, "provenance": deepcopy(PROVENANCE)})
            store.put(db, "response_study_preview", identity.tenant_id, record, project_id)
            return record

    @router.get(prefix + "/previews/{preview_id}")
    def read_preview(project_id: str, preview_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        record = checked(scoped(store, "response_study_preview", preview_id, identity, project_id))
        if record["created_by"] != identity.user_id:
            error("NOT_FOUND", "Preview not found for this user", 404)
        return record

    @router.post(prefix, status_code=201)
    def commit(project_id: str, body: ResponseStudyCommitRequest,
               identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            required(store, "project", project_id, identity, db)
            preview = checked(scoped(store, "response_study_preview", body.preview_id, identity, project_id, db))
            if preview["created_by"] != identity.user_id:
                error("NOT_FOUND", "Preview not found for this user", 404)
            if preview["content_hash"] != body.preview_hash:
                error("RESPONSE_STUDY_REVIEW_MISMATCH", "Commit must identify the exact reviewed preview", 409)
            receipt_id = "response_commit_" + digest({"tenant": identity.tenant_id, "project": project_id,
                                                       "user": identity.user_id, "key": body.idempotency_key}).split(":")[1][:40]
            receipt = store.get("response_study_commit", receipt_id, identity.tenant_id, db)
            if receipt:
                checked(receipt)
                if receipt["request_hash"] != digest(body.model_dump()):
                    error("IDEMPOTENCY_CONFLICT", "This confirmation key belongs to a different reviewed preview", 409)
                return checked(scoped(store, "response_study", receipt["study_id"], identity, project_id, db))
            request = ResponseStudyPreviewRequest.model_validate(preview["request"])
            gate = authorizer.check()
            parent, brief = parent_and_brief(store, project_id, request, identity, gate, db)
            studies = records(db, "response_study", identity, project_id)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            if len(studies) >= 50 or sum(datetime.fromisoformat(s["created_at"]) >= cutoff for s in studies) >= 10:
                error("RESPONSE_STUDY_QUOTA", "Study allowance reached: 10 per 24 hours and 50 per project", 429)
            pending, candidates = [], []
            for factor, reviewed in zip(request.factors, preview["candidates"], strict=True):
                core, current = candidate(store, project_id, request, parent, brief, identity, factor)
                if current != reviewed:
                    error("RESPONSE_STUDY_REVIEW_MISMATCH", "Candidate no longer matches the reviewed preview", 409)
                if current["validation"].get("valid") is not True:
                    error("PLANNING_CONSTRAINT_FAILED", "Every candidate must pass geometry and declared requirements", 422, current["validation"].get("issues"))
                scenario = scenario_version(core, project_id, current["name"], parent, current["operations"])
                permission(store, scenario, identity, gate, db)
                if physical_hash(scenario) != current["scenario_hash"]:
                    error("RESPONSE_STUDY_INTEGRITY_ERROR", "Candidate physical identity changed", 503)
                pending.append(scenario)
                candidates.append({**current, "scenario_id": scenario["id"], "scenario_version": scenario["version"]})
            record = seal({"id": new_id("response_study"), "project_id": project_id, "name": request.name,
                           "created_at": utc_now(), "created_by": identity.user_id,
                           "preview_id": preview["id"], "preview_hash": preview["content_hash"],
                           "parent_scenario_id": parent["id"], "parent_hash": parent["content_hash"], "parent_version": parent["version"],
                           "brief_id": brief["id"], "brief_hash": brief["content_hash"], "source_ids": request.source_ids,
                           "candidates": candidates, "qualified": False, "provenance": deepcopy(PROVENANCE),
                           "authorization": {"decision_id": gate.decision_id, **gate.headers()}})
            for scenario in pending:
                store.put(db, "scenario", identity.tenant_id, scenario, project_id)
            store.put(db, "response_study", identity.tenant_id, record, project_id)
            store.put(db, "response_study_commit", identity.tenant_id,
                      seal({"id": receipt_id, "project_id": project_id, "study_id": record["id"], "request_hash": digest(body.model_dump())}), project_id)
            return record

    @router.get(prefix)
    def list_studies(project_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        return sorted([checked(s) for s in store.list("response_study", identity.tenant_id, project_id)], key=lambda s: s["created_at"], reverse=True)

    @router.get(prefix + "/{study_id}")
    def read_study(project_id: str, study_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        return checked(scoped(store, "response_study", study_id, identity, project_id))

    @router.post(prefix + "/{study_id}/compare")
    def compare_study(project_id: str, study_id: str, body: ResponseStudyCompareRequest,
                      identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        study = checked(scoped(store, "response_study", study_id, identity, project_id))
        selected = next((c for c in study["candidates"] if c["scenario_id"] == body.candidate_scenario_id), None)
        if not selected:
            error("RESPONSE_STUDY_CANDIDATE_SCOPE", "Candidate does not belong to this response study", 404)
        for run_id, scenario_id, scenario_hash in [(body.baseline_run_id, study["parent_scenario_id"], study["parent_hash"]),
                                                   (body.alternative_run_id, selected["scenario_id"], selected["scenario_hash"])]:
            run = store.run(run_id, identity.tenant_id)
            if not run:
                error("NOT_FOUND", "Run not found in this project/tenant", 404)
            project_resource(run, project_id)
            manifest = run.get("manifest", {})
            if run["scenario_id"] != scenario_id or manifest.get("scenario_id") != scenario_id or manifest.get("scenario_hash") != scenario_hash:
                error("RESPONSE_STUDY_RUN_MISMATCH", "Runs must identify the exact parent and selected candidate", 409)
            if physical_hash(manifest.get("inputs", {})) != scenario_hash or manifest.get("planning_brief_hash") != study["brief_hash"]:
                error("RESPONSE_STUDY_RUN_MISMATCH", "Run inputs and frozen planning requirements must match this study", 409)
        result = compare(store, project_id, ComparisonRequest(baseline_run_id=body.baseline_run_id, alternative_run_id=body.alternative_run_id), identity)
        return {"study_id": study["id"], "study_hash": study["content_hash"], "candidate_scenario_id": selected["scenario_id"],
                "comparison": result, "qualified": False, "optimal": False}

    return router
