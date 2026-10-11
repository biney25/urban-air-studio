"""Fixed server-authoritative tools. No generic network, shell, SQL or mutations."""
from copy import deepcopy
import math
import re
from fastapi import HTTPException
from api.auth import Identity
from api.schemas import OperationsRequest, ComparisonRequest
from api.storage import digest

LIMITATIONS = [
    "Research preview: physical accuracy and decision adequacy are not established.",
    "The assistant explains retained evidence; it cannot certify air quality, health or no-harm.",
    "Proposed edits require your review and the application's normal validation.",
]


def validate_geometry_patch(parent, request):
    """Same immutable operation semantics as the existing normal edit endpoint."""
    from science.geometry import validate_scenario
    candidate = deepcopy(parent)
    for operation in request.operations:
        group = {"building_update": "buildings", "source_update": "sources", "receptor_update": "receptors"}[operation.type]
        obj = next((row for row in candidate[group] if row["id"] == operation.object_id), None)
        if obj is None:
            raise ValueError("The proposed object is absent from the selected snapshot")
        changes = operation.changes.model_dump(exclude_none=True)
        if not changes:
            raise ValueError("An actual parameter change is required")
        # Assessment support is fixed for this assistant task, even if unprotected.
        if group == "receptors":
            raise ValueError("The assistant cannot move assessment locations")
        if group == "buildings":
            footprint = obj["footprint"]
            cx = sum(p[0] for p in footprint) / len(footprint)
            cy = sum(p[1] for p in footprint) / len(footprint)
            theta = math.radians(changes.get("rotation_deg", 0))
            obj["footprint"] = [[cx + (p[0]-cx)*math.cos(theta) - (p[1]-cy)*math.sin(theta) + changes.get("dx", 0),
                                 cy + (p[0]-cx)*math.sin(theta) + (p[1]-cy)*math.cos(theta) + changes.get("dy", 0)] for p in footprint]
            if "height_m" in changes:
                obj["height_m"] = changes["height_m"]
        else:
            obj.update(changes)
    candidate["diagnostics"] = validate_scenario(candidate)
    return candidate


class StudyTools:
    NAMES = {"inspect_study", "read_geometry", "read_comparison", "propose_change"}

    def __init__(self, *, project, selected_scenario, body, identity, store, authorizer,
                 required, project_resource, compare, constraint_validator=None):
        self.project, self.scenario, self.body = project, selected_scenario, body
        self.identity, self.store, self.authorizer = identity, store, authorizer
        self.required, self.project_resource, self.compare = required, project_resource, compare
        self.constraint_validator = constraint_validator
        self.calls, self.evidence, self.proposal, self.results, self.inputs = [], {}, None, [], []

    def add_evidence(self, kind, identifier, label, details=None):
        self.evidence[(kind, identifier)] = {"kind": kind, "id": identifier, "label": label, "details": details}

    def permit(self, scenario):
        check = self.authorizer.check()
        check.require(self.identity, self.project["id"], action="derive", identifier=scenario["id"],
                      tenant_id=self.identity.tenant_id, resource_project_id=scenario["project_id"],
                      rights=scenario.get("source_rights", {}))
        # Keep dependency authority where it already exists; no model supplied rights.
        from api.phase_b import allocation_dependencies
        def fail(code, message, status=400, issues=None):
            raise HTTPException(status, {"code": code, "message": message})
        def enforce(record):
            rights = record.get("effective_rights", record.get("source_rights", record.get("rights")))
            check.require(self.identity, self.project["id"], action="derive", identifier=record["id"],
                          tenant_id=self.identity.tenant_id, resource_project_id=record["project_id"], rights=rights)
        allocation_dependencies(self.store, scenario, self.identity.tenant_id, fail, require=enforce)

    def require_scenario(self):
        if self.scenario is None:
            raise ValueError("Select a saved design first")
        if self.scenario.get("content_hash") != self._physical_hash(self.scenario):
            raise ValueError("Selected snapshot identity failed integrity verification")
        self.permit(self.scenario)
        return self.scenario

    @staticmethod
    def _physical_hash(scenario):
        from api.main import physical_hash
        return physical_hash(scenario)

    def invoke(self, name, arguments):
        from science.geometry import ScienceError
        if name not in self.NAMES or not isinstance(arguments, dict):
            raise ValueError("Assistant tool is outside the fixed application authority")
        if len(self.calls) >= 6:
            raise ValueError("Assistant tool-call allowance exhausted")
        self.inputs.append({"name": name, "arguments": deepcopy(arguments)})
        before = set(self.evidence)
        status = "complete"
        try:
            method = getattr(self, name)
            result = method(**arguments)
        except HTTPException as exc:
            status = "blocked"
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            result = {"blocked": True, "code": detail.get("code", "PERMISSION_DENIED"),
                      "message": "This operation is not permitted for the selected study"}
        except (ValueError, TypeError, KeyError) as exc:
            status = "blocked"
            result = {"blocked": True, "code": "TOOL_INPUT_BLOCKED", "message": str(exc)[:300]}
        except ScienceError as exc:
            status = "blocked"
            result = {"blocked": True, "code": exc.code, "message": exc.message[:300], "issues": exc.issues}
        evidence_ids = [key[1] for key in set(self.evidence)-before]
        # Repeated calls still cite their actual retained support.
        if not evidence_ids:
            evidence_ids = [row["id"] for row in self.evidence.values()]
        self.calls.append({"name": name, "status": status, "evidence_ids": sorted(evidence_ids)})
        self.results.append(result)
        return result

    def inspect_study(self):
        self.add_evidence("project", self.project["id"], "Selected study")
        scenarios = self.store.list("scenario", self.identity.tenant_id, self.project["id"])
        runs = self.store.list_runs(self.identity.tenant_id, self.project["id"])
        return {"project_id": self.project["id"], "baseline_scenario_id": self.project.get("baseline_scenario_id"),
                "selected_scenario_id": self.scenario["id"] if self.scenario else None,
                "saved_scenarios": len(scenarios), "runs": [{"id": r["id"], "status": r["status"]} for r in runs[-12:]],
                "blockers": ([] if self.project.get("baseline_scenario_id") else ["Freeze a reviewed baseline before studying an alternative"]),
                "qualification": "research_preview", "limitations": LIMITATIONS}

    def read_geometry(self):
        scenario = self.require_scenario()
        buildings = [{"id": r["id"], "height_m": r["height_m"]} for r in scenario["buildings"]]
        sources = [{k: r[k] for k in ("id", "x", "y", "z", "rate_g_s")} for r in scenario["sources"]]
        self.add_evidence("scenario", scenario["id"], "Selected design snapshot", {"buildings": buildings[:50], "sources": sources[:50]})
        return {"scenario_id": scenario["id"], "version": scenario["version"],
                "buildings": buildings[:50], "sources": sources[:50],
                "assessment_locations": len(scenario["receptors"]),
                "geometry_diagnostics": scenario.get("diagnostics", {})}

    def read_comparison(self):
        if not self.body.baseline_run_id or not self.body.alternative_run_id:
            raise ValueError("Choose both baseline and alternative runs first")
        for identifier in (self.body.baseline_run_id, self.body.alternative_run_id):
            run = self.store.run(identifier, self.identity.tenant_id)
            if not run or run["project_id"] != self.project["id"]:
                raise ValueError("Run is absent from this study")
            scenario = self.project_resource(self.required(self.store, "scenario", run["scenario_id"], self.identity), self.project["id"])
            self.permit(scenario)
        result = self.compare(self.store, self.project["id"], ComparisonRequest(
            baseline_run_id=self.body.baseline_run_id, alternative_run_id=self.body.alternative_run_id), self.identity)
        for identifier, label in ((self.body.baseline_run_id, "Baseline research run"), (self.body.alternative_run_id, "Alternative research run")):
            self.add_evidence("run", identifier, label)
        metrics = [{**{k: row[k] for k in ("receptor_id", "baseline_ug_m3", "alternative_ug_m3", "delta_ug_m3", "percent_change", "protected", "valid")},
                    "metric_id": "metric_" + digest({"baseline": result["baseline_run_id"], "alternative": result["alternative_run_id"], "metric": row})[7:]}
                   for row in result["metrics"][:50]]
        for metric in metrics:
            self.add_evidence("evidence", metric["metric_id"], "Matched research metric", {**metric, "units": result["units"], "species": result["species"],
                              "baseline_run_id": result["baseline_run_id"], "alternative_run_id": result["alternative_run_id"]})
        return {"baseline_run_id": result["baseline_run_id"], "alternative_run_id": result["alternative_run_id"],
                "metrics": metrics,
                "units": result["units"], "species": result["species"], "qualification": "research_preview",
                "interpretation": "These values do not establish a real pollution or health improvement; without uncertainty, small differences are inconclusive."}

    def propose_change(self, object_id: str, change: str, value: float):
        scenario = self.require_scenario()
        if self.proposal is not None:
            raise ValueError("One proposal per message is permitted")
        group = next((group for group in ("buildings", "sources") if any(row["id"] == object_id for row in scenario[group])), None)
        allowed = {"buildings": {"height_m", "dx", "dy", "rotation_deg"}, "sources": {"rate_g_s", "x", "y", "z"}}
        if group is None or change not in allowed[group]:
            raise ValueError("Object/change does not belong to the selected editable snapshot")
        request = OperationsRequest(expected_version=scenario["version"], operations=[{
            "type": "building_update" if group == "buildings" else "source_update",
            "object_id": object_id, "changes": {change: value}}])
        candidate = validate_geometry_patch(scenario, request)
        if self._physical_hash(candidate) == scenario["content_hash"]:
            raise ValueError("The proposal leaves the selected design unchanged")
        programme = self.constraint_validator(self.project["id"], scenario, candidate, self.identity, self.store) if self.constraint_validator else {
            "valid": False, "status": "unconfigured", "issues": [{"code": "CONSTRAINT_REVIEW_REQUIRED", "message": "Review programme constraints before applying this suggestion"}]}
        self.proposal = {"scenario_id": scenario["id"], "expected_version": scenario["version"],
                         "operations": request.model_dump()["operations"], "input_hash": scenario["content_hash"],
                         "requires_confirmation": True, "validation": {"valid": bool(programme.get("valid")),
                         "geometry_valid": True, "programme_status": programme.get("status", "unknown"), "issues": programme.get("issues", []),
                         "brief_id": programme.get("brief_id"), "brief_hash": programme.get("brief_hash"), "evaluations": programme.get("evaluations", [])}}
        self.add_evidence("scenario", scenario["id"], "Unchanged proposal input snapshot")
        # Exact numbers/operations are rendered from the saved proposal by the UI.
        # Keep them out of model prose; only authoritative status crosses back.
        summary = "The proposal passes the declared geometry and programme constraints and awaits your review. No edit was applied. Physical benefit is not established." if programme.get("valid") else "The proposal requires constraint review before it can be applied. No edit was applied. Physical benefit is not established."
        return {"proposal_created": True, "scenario_id": scenario["id"], "requires_confirmation": True,
                "validation": self.proposal["validation"], "applied": False, "physical_benefit": "Not established", "user_summary": summary}


def supported_answer(text, tools):
    """Reject model metrics/foreign IDs; the UI always has exact authoritative evidence."""
    if not isinstance(text, str) or not 1 <= len(text) <= 3000 or not tools.calls:
        return False
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        return False
    if re.search(r"\b(?:https?://|file://|javascript:|data:|<script|<iframe)", text, re.I):
        return False
    identifiers = re.findall(r"\b(?:project|scenario|run|report|authz|dcase|evidence)_[a-zA-Z0-9_-]+", text)
    allowed = {row["id"] for row in tools.evidence.values()}
    if any(identifier not in allowed for identifier in identifiers):
        return False
    # Numerical statements must be rendered from exact metric records, never LLM prose.
    # Known retained IDs may contain digits; remove only those exact citations first.
    prose = text
    for identifier in allowed:
        prose = prose.replace(identifier, "")
    if re.search(r"\d", prose):
        return False
    # No model-generated pollution/AQI/health scores may be shown.
    if re.search(r"(?:\bAQI\s*(?:is|=|of|:)?\s*\d|\bPM\s*(?:2[.,]5|10)\s*(?:is|=|of|:)?\s*\d|\b(?:safe|healthier|cleaner)\s+(?:air|breathing)|\b(?:validated|guaranteed|proven)\s+(?:accuracy|improvement|safe|reduction))", text, re.I):
        return False
    return True
