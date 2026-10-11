"""Versioned planner requirements and deterministic geometry-only feasibility.

These checks do not establish environmental accuracy, access-code compliance,
planning consent or physical no-harm. Every numerical capacity assumption is
declared, and run manifests freeze the requirements used for that study.
"""
from copy import deepcopy
import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from shapely.geometry import Polygon

from api.schemas import Input, Number, Identifier
from api.storage import Store, canonical, digest, new_id, utc_now


class SiteSelection(Input):
    latitude: Annotated[Number, Field(ge=-85, le=85)]
    longitude: Annotated[Number, Field(ge=-180, le=180)]
    width_km: Annotated[Number, Field(ge=0.25, le=1)]

    @model_validator(mode="after")
    def supported_width(self):
        if self.width_km not in {0.25, 0.5, 1}:
            raise ValueError("Select a 250 m, 500 m or 1 km study area")
        return self


class ProjectCreate(Input):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    category: Literal["user", "sample", "internal_test"] = "user"
    planning_question: Annotated[str | None, Field(min_length=1, max_length=1000)] = None
    site: SiteSelection | None = None


class ProjectCategory(Input):
    category: Literal["user", "sample", "internal_test"]


class AccessCorridor(Input):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=120)]
    footprint: Annotated[list[tuple[Number, Number]], Field(min_length=3, max_length=200)]

    @model_validator(mode="after")
    def valid_polygon(self):
        if any(abs(n) > 1500 for point in self.footprint for n in point):
            raise ValueError("Access corridor must use the site's local metre frame")
        polygon = Polygon(self.footprint)
        if not polygon.is_valid or polygon.is_empty or polygon.area < 0.1:
            raise ValueError("Access corridor needs a valid, non-empty polygon")
        return self


class PlanningBrief(Input):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    question: Annotated[str, Field(min_length=1, max_length=1000)]
    species: Literal["passive_tracer"] = "passive_tracer"
    storey_height_m: Annotated[Number | None, Field(ge=2, le=8)] = None
    min_floor_area_m2: Annotated[Number | None, Field(ge=0, le=1_000_000)] = None
    min_building_gap_m: Annotated[Number | None, Field(ge=0, le=100)] = None
    access_corridors: Annotated[list[AccessCorridor], Field(max_length=30)] = []
    protected_location_ids: Annotated[list[Identifier], Field(max_length=200)] = []

    @model_validator(mode="after")
    def declared_capacity(self):
        if self.min_floor_area_m2 is not None and self.storey_height_m is None:
            raise ValueError("Declare storey height before checking programme floor area")
        if len(set(self.protected_location_ids)) != len(self.protected_location_ids):
            raise ValueError("Protected assessment locations must be unique")
        if len({c.id for c in self.access_corridors}) != len(self.access_corridors):
            raise ValueError("Access corridor identifiers must be unique")
        return self


class ConstraintRequest(Input):
    scenario_id: Identifier


def latest_brief(store, project_id, identity, db=None):
    project = store.get("project", project_id, identity.tenant_id, db)
    identifier = (project or {}).get("planning_brief_id")
    if not identifier:
        return None
    brief = store.get("planning_brief", identifier, identity.tenant_id, db)
    if not brief or brief.get("project_id") != project_id or brief.get("content_hash") != digest({k: v for k, v in brief.items() if k != "content_hash"}):
        raise HTTPException(status_code=503, detail={"code":"BRIEF_INTEGRITY_ERROR", "message":"Stored planning requirements failed integrity checks; the operation is blocked"})
    return brief


def check_constraints(scenario, brief, parent=None):
    if not brief:
        return {"valid": False, "status": "unconfigured", "issues": [{"code": "CONSTRAINT_REVIEW_REQUIRED", "message": "Declare the planning question and requirements before calling an alternative feasible."}], "evaluations": [], "qualification": "geometry_only"}
    rules = brief["requirements"]
    issues, evaluations = [], []
    polygons = [(b["id"], Polygon(b["footprint"])) for b in scenario["buildings"]]
    floor_area = None
    if rules.get("storey_height_m") is not None:
        height = rules["storey_height_m"]
        floor_area = sum(p.area * math.floor(b["height_m"] / height + 1e-10) for b, (_, p) in zip(scenario["buildings"], polygons))
        evaluations.append({"rule": "declared_capacity", "value_m2": floor_area, "storey_height_m": height, "basis": "Footprint × whole storeys from explicitly assumed storey height; not surveyed usable floor area"})
    if rules.get("min_floor_area_m2") is not None and floor_area < rules["min_floor_area_m2"]:
        issues.append({"code": "CAPACITY_BELOW_REQUIREMENT", "message": "Alternative does not preserve the declared floor-area requirement", "actual_m2": floor_area, "required_m2": rules["min_floor_area_m2"]})
    gaps = [a.distance(b) for i, (_, a) in enumerate(polygons) for _, b in polygons[i + 1:]]
    gap = min(gaps) if gaps else None
    if rules.get("min_building_gap_m") is not None:
        evaluations.append({"rule": "minimum_building_gap", "actual_m": gap, "required_m": rules["min_building_gap_m"], "applicable": gap is not None})
        if gap is not None and gap + 1e-8 < rules["min_building_gap_m"]:
            issues.append({"code": "BUILDING_GAP_BLOCKED", "message": "Building gap is below the declared minimum", "actual_m": gap, "required_m": rules["min_building_gap_m"]})
    for corridor in rules.get("access_corridors", []):
        path = Polygon(corridor["footprint"])
        blocking = [identifier for identifier, polygon in polygons if path.intersection(polygon).area > 1e-6]
        evaluations.append({"rule": "access_corridor", "id": corridor["id"], "name": corridor["name"], "clear": not blocking, "blocking_building_ids": blocking})
        if blocking:
            issues.append({"code": "ACCESS_CORRIDOR_BLOCKED", "message": "A building overlaps the declared access corridor", "corridor_id": corridor["id"], "building_ids": blocking})
    candidates = {r["id"]: r for r in scenario["receptors"]}
    protected = set(rules.get("protected_location_ids", []))
    protected.update(r["id"] for r in (parent or scenario)["receptors"] if r.get("protected"))
    previous = {r["id"]: r for r in (parent or scenario)["receptors"]}
    for identifier in protected:
        record = candidates.get(identifier)
        if not record:
            issues.append({"code": "PROTECTED_LOCATION_MISSING", "message": "A protected assessment location is absent", "object_id": identifier})
        elif identifier in previous and any(record[k] != previous[identifier][k] for k in ("x", "y", "z", "protected")):
            issues.append({"code": "PROTECTED_LOCATION_CHANGED", "message": "Protected locations must keep the same sampling support in a design comparison", "object_id": identifier})
    if parent and any(scenario.get(k) != parent.get(k) for k in ("origin", "coordinate_frame", "analysis_domain")):
        issues.append({"code": "SITE_FRAME_CHANGED", "message": "Alternative must retain the baseline's coordinate frame and analysis domain"})
    return {"valid": not issues, "status": "fail" if issues else "pass", "issues": issues, "evaluations": evaluations, "brief_id": brief["id"], "brief_hash": brief["content_hash"], "qualification": "geometry_only", "limitations": ["Declared assumptions, not measured programme area or access-code certification", "Environmental no-harm and physical benefit are not established by these checks"]}


def validate_programme(project_id, parent_scenario, candidate_scenario, identity, store):
    if parent_scenario.get("project_id") != project_id or candidate_scenario.get("project_id") != project_id:
        return {"valid": False, "status": "fail", "issues": [{"code": "PROJECT_SCOPE_MISMATCH", "message": "Proposal belongs to another project"}], "evaluations": []}
    return check_constraints(candidate_scenario, latest_brief(store, project_id, identity), parent_scenario)


def make_router(*, identity_dependency, store_dependency, required, project_resource, error):
    router = APIRouter()

    @router.post("/api/projects/{project_id}/category")
    def category(project_id: str, body: ProjectCategory, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            project = required(store, "project", project_id, identity, db)
            project["category"] = body.category
            db.execute("UPDATE objects SET data=? WHERE kind='project' AND id=? AND tenant_id=?", (canonical(project), project_id, identity.tenant_id))
        return project

    @router.get("/api/projects/{project_id}/planning-brief")
    def get_brief(project_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        return latest_brief(store, project_id, identity)

    @router.post("/api/projects/{project_id}/planning-brief", status_code=201)
    def save_brief(project_id: str, body: PlanningBrief, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            project = required(store, "project", project_id, identity, db)
            old = latest_brief(store, project_id, identity, db)
            if body.expected_revision != (old["revision"] if old else 0):
                error("BRIEF_REVISION_CONFLICT", "Planning requirements changed; reload before saving", 409)
            requirements = body.model_dump(exclude={"expected_revision"})
            brief = {"id": new_id("brief"), "project_id": project_id, "revision": body.expected_revision + 1, "parent_id": old["id"] if old else None, "created_at": utc_now(), "requirements": requirements, "qualification": "declared_planning_requirements"}
            brief["content_hash"] = digest(brief)
            Store.put(db, "planning_brief", identity.tenant_id, brief, project_id)
            project.update(planning_brief_id=brief["id"], planning_question=body.question)
            db.execute("UPDATE objects SET data=? WHERE kind='project' AND id=? AND tenant_id=?", (canonical(project), project_id, identity.tenant_id))
        return brief

    @router.post("/api/projects/{project_id}/constraints/check")
    def constraints(project_id: str, body: ConstraintRequest, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        project = required(store, "project", project_id, identity)
        scenario = project_resource(required(store, "scenario", body.scenario_id, identity), project_id)
        baseline = required(store, "scenario", project["baseline_scenario_id"], identity) if project.get("baseline_scenario_id") else scenario
        return validate_programme(project_id, baseline, scenario, identity, store)

    return router
