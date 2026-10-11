"""Purpose-specific, server-checked research packages, never raw context export.

An ODbL report grant applies only to a package containing attribution and the
machine-readable derivative/alterations. It never changes the original provider
display-context restrictions or the scientific qualification of a result.
"""
from copy import deepcopy
import os
from fastapi import APIRouter, Depends, Response
from api.authorization import Authorizer, PermissionCheck
from api.storage import digest


POLICY = {
    "id":"odbl-local-research-package-v1",
    "licence":"ODbL 1.0",
    "licence_url":"https://opendatacommons.org/licenses/odbl/1-0/",
    "purpose":"Complete internal research report/reproduction package",
    "required_contents":["source attribution", "selected original database objects", "declared alterations", "machine-readable derivative scenario"],
    "public_release":"Not automatic; ODbL notices/share-alike obligations and any other source permissions still apply",
}


def admitted_package(store, scenario, identity, error, authorizer=None):
    from api.main import physical_hash, reject_inventory_preview
    reject_inventory_preview(scenario)
    if physical_hash(scenario) != scenario.get("content_hash"):
        error("SCENARIO_INTEGRITY_ERROR", "Scenario contents do not match their immutable identity", 503)
    check = PermissionCheck(authorizer or Authorizer())
    project_id = scenario["project_id"]
    if not scenario.get("source_preparation"):
        check.require(identity, project_id, action="export", identifier=scenario["id"], tenant_id=identity.tenant_id,
                      resource_project_id=project_id, rights=scenario.get("source_rights", {}))
        return {"schema_version":"research-export-v1", "scenario":deepcopy(scenario), "source_compliance":None,
                "omissions":["Basemap imagery and unrelated/private source context", "No independently qualified physical prediction"],
                "authorization":{"decision_id":check.decision_id, "decision":"Allow", **check.headers()}, "qualified":False}
    if os.getenv("UA_MODE", "local") != "local":
        error("EXPORT_SCOPE_UNAVAILABLE", "The reviewed ODbL bundle path is currently limited to local research", 409)
    from api.site_preparation import checked_preparation_dependency
    record = checked_preparation_dependency(store, scenario, identity.tenant_id, error)
    bundle = deepcopy(record["compliance_bundle"])
    needed = ["selected_original_objects","height_alterations","exclusions","semantic_geojson","conversion_method","attribution","licence_url"]
    if bundle.get("schema_version") != "osm-local-study-compliance-v1" or any(k not in bundle for k in needed):
        error("EXPORT_COMPLIANCE_INCOMPLETE", "The source's required derivative/alteration package is incomplete", 403)
    # This reviewed use grant is distinct from original context rights and from
    # the preparation's private solver-only grant. It cannot export just a PNG
    # or raw scenario while omitting the licence/database package.
    grant = {"policy":deepcopy(POLICY), "policy_hash":digest(POLICY), "scenario_id":scenario["id"],
             "scenario_hash":scenario["content_hash"], "preparation_hash":record["content_hash"],
             "compliance_bundle_hash":bundle["content_hash"], "rights":{"derive":True,"solve":True,"export":True},
             "scope":"complete_local_research_bundle_only", "qualified":False}
    grant["content_hash"] = digest(grant)
    check.require(identity, project_id, action="export", identifier=grant["content_hash"], tenant_id=identity.tenant_id,
                  resource_project_id=project_id, rights=grant["rights"])
    return {"schema_version":"research-export-v1", "scenario":deepcopy(scenario),
            "source_compliance":{"grant":grant,"original_reviewed_derivative":bundle,
                                 "current_derivative":deepcopy(scenario), "additional_alterations":deepcopy(scenario.get("operations", [])),
                                 "notice":"© OpenStreetMap contributors. Contains data adapted from OpenStreetMap under ODbL 1.0. The included original objects, alterations and derivative are provided for source/licence interpretation and local reproduction.",
                                 "licence_url":POLICY["licence_url"], "attribution_url":"https://www.openstreetmap.org/copyright"},
            "omissions":["Basemap imagery, weather context and unrelated provider objects", "Private review identities and raw full provider responses", "No independently qualified physical prediction"],
            "authorization":{"decision_id":check.decision_id,"decision":"Allow", **check.headers()}, "qualified":False}


def make_router(*, identity_dependency, store_dependency, required, error, authorizer):
    router = APIRouter(tags=["Permitted research exports"])

    @router.get("/api/scenarios/{scenario_id}/export")
    def scenario_export(scenario_id: str, response: Response, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        scenario = required(store, "scenario", scenario_id, identity)
        package = admitted_package(store, scenario, identity, error, authorizer)
        package["content_hash"] = digest(package)
        response.headers["Content-Disposition"] = f'attachment; filename="{scenario_id}-research.json"'
        response.headers["X-Authorization-Decision-ID"] = package["authorization"]["decision_id"]
        return package

    @router.get("/api/runs/{run_id}/export")
    def run_export(run_id: str, response: Response, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        from api.main import required_run, read_run_bundle
        run = required_run(store, run_id, identity)
        package = admitted_package(store, run["manifest"]["inputs"], identity, error, authorizer)
        package["run_manifest"] = deepcopy(run["manifest"])
        if run["status"] == "complete_unqualified":
            committed = read_run_bundle(store, run)
            package["result"] = committed["result"]
            package["artifact_manifest"] = committed["artifact_manifest"]
        else:
            package["omissions"].append("No completed result is available for this run")
        package["content_hash"] = digest(package)
        response.headers["Content-Disposition"] = f'attachment; filename="{run_id}-research.json"'
        response.headers["X-Authorization-Decision-ID"] = package["authorization"]["decision_id"]
        return package

    return router
