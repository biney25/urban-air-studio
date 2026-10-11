"""Phase A API: verified identity and immutable, scoped study execution."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import base64
import html
import json
import logging
import math
import os
from pathlib import Path
from urllib.parse import urlsplit
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse

from api.schemas import (BaselineRequest, ComparisonRequest, ImportRequest, Named,
                         OperationsRequest, QuoteRequest, ReportRequest, RunRequest)
from api.storage import ACTIVE, TERMINAL, Store, canonical, digest, new_id, utc_now
from api import responses as response
from api.auth import Identity, request_identity, public_config
from api.readiness import Readiness
from api.planning import ProjectCreate, check_constraints, latest_brief, validate_programme


class BoundedBodyMiddleware:
    """Bound actual received bytes before parsing, including chunked/no-length requests."""
    def __init__(self, app, maximum_bytes=5_000_000):
        self.app, self.maximum_bytes = app, maximum_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope,receive,send)
        messages, count = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            count += len(message.get("body",b""))
            if count > self.maximum_bytes:
                response = JSONResponse(status_code=413,content={"detail":{"code":"IMPORT_LIMIT_EXCEEDED","message":"Actual request body exceeds 5 MB local allowance"}},headers={"Cache-Control":"no-store"})
                return await response(scope,receive,send)
            messages.append(message)
            if not message.get("more_body",False):
                break
        iterator = iter(messages)
        async def replay():
            try:
                return next(iterator)
            except StopIteration:
                return await receive()
        await self.app(scope,replay,send)


def error(code, message, status=400, issues=None):
    detail = {"code":code, "message":message}
    if issues is not None:
        detail["issues"] = issues
    raise HTTPException(status_code=status, detail=detail)


def local_identity(request: Request):
    # Kept as a stable dependency override seam for isolated integration tests.
    return request_identity(request)


def get_store(request: Request):
    if not hasattr(request.app.state, "store"):
        if os.getenv("UA_MODE", "local") != "local" and not (os.getenv("UA_DATABASE_URL") and os.getenv("UA_ASSET_BUCKET")):
            error("PLATFORM_NOT_CONFIGURED", "Verified identity requires PostgreSQL/PostGIS and private object storage", 503)
        request.app.state.store = Store()
    if os.getenv("UA_MODE", "local") != "local" and request.app.state.store.adapter_name != "postgres-postgis-s3":
        error("PLATFORM_NOT_CONFIGURED", "Public identity cannot use the local development storage adapter", 503)
    return request.app.state.store


def required(store, kind, identifier, identity, db=None):
    value = store.get(kind, identifier, identity.tenant_id, db)
    if not value:
        error("NOT_FOUND", "Resource not found in this project/tenant", 404)
    return value


def project_resource(value, project_id):
    if value["project_id"] != project_id:
        error("PROJECT_SCOPE_MISMATCH", "Resource belongs to a different project", 404)
    return value


def physical_hash(scenario):
    keys = ["origin", "coordinate_frame", "analysis_domain", "buildings", "sources", "receptors", "source_rights", "source_label"]
    if scenario.get("source_preparation"):
        keys.append("source_preparation")
    return digest({key:scenario.get(key) for key in keys})


def require_scenario_permission(store, scenario, identity, action, db=None):
    from api.authorization import Authorizer, PermissionCheck
    if scenario.get("source_preparation"):
        from api.site_preparation import checked_preparation_dependency
        checked_preparation_dependency(store, scenario, identity.tenant_id, error, db)
    gate = PermissionCheck(Authorizer())
    gate.require(identity, scenario["project_id"], action=action, identifier=scenario["id"],
                 tenant_id=identity.tenant_id, resource_project_id=scenario["project_id"], rights=scenario.get("source_rights", {}))
    return gate


def reject_inventory_preview(scenario):
    from api.preview_scope import inventory_bound,CODE,MESSAGE
    if inventory_bound(scenario):
        error(CODE,MESSAGE,409)


def scenario_version(core, project, name, parent=None, operations=None):
    result = deepcopy(core)
    result.update(id=new_id("scenario"), project_id=project, name=name,
                  version=(parent["version"]+1 if parent else 1),
                  parent_id=(parent["id"] if parent else None), created_at=utc_now())
    result["operations"] = (parent.get("operations", []) if parent else []) + (operations or [])
    result["content_hash"] = physical_hash(result)
    return result


def read_run_bundle(store, run):
    try:
        bundle = store.read_artifact(run["_artifact"], run.get("artifact_checksum"))
        manifest = bundle["artifact_manifest"]
        if manifest["run_id"] != run["id"] or manifest["input_manifest_hash"] != digest(run["manifest"]) or manifest["result_hash"] != digest(bundle["result"]):
            raise ValueError("Artifact manifest dependency mismatch")
        return bundle
    except (OSError, ValueError, KeyError, TypeError):
        error("ARTIFACT_INTEGRITY_ERROR", "Committed result bundle failed integrity checks; inspect local storage", 503)


def scientific_call(function, *args):
    from science.geometry import ScienceError
    try:
        return function(*args)
    except ScienceError as exc:
        error(exc.code, exc.message, 422, exc.issues)


def public_run(store, run):
    reject_inventory_preview(run.get("manifest",{}).get("inputs"))
    result = {key:value for key,value in run.items() if not key.startswith("_")}
    if run["status"] == "complete_unqualified" and run.get("_artifact"):
        result["result"] = read_run_bundle(store, run)["result"]
    return result


def required_run(store, identifier, identity):
    value = store.run(identifier, identity.tenant_id)
    if not value:
        error("NOT_FOUND", "Run not found in this tenant", 404)
    return value


def new_run(project, scenario, quote, attempt=1, retry_of=None):
    reject_inventory_preview(scenario)
    from science import MODEL_VERSION, SCIENCE_BUILD_HASH, SCIENCE_SOURCE_HASHES, DEPENDENCY_VERSIONS
    manifest = {"schema_version":"phase-a-run-v1", "species":"passive_tracer", "tier":"T1",
                "model_version":MODEL_VERSION, "conditions":quote["conditions"],
                "science_build_hash":SCIENCE_BUILD_HASH,"science_source_hashes":SCIENCE_SOURCE_HASHES,
                "dependency_versions":DEPENDENCY_VERSIONS,
                "scenario_id":scenario["id"], "scenario_hash":scenario["content_hash"],
                "inputs":deepcopy(scenario), "conditions_hash":digest(quote["conditions"]),
                "rights":scenario.get("source_rights", {}), "evidence_kind":"simulated",
                "qualification":"research_preview", "validation_records":qualification_records(),
                "resource_allowance_seconds":quote["maximum_seconds"]/quote["cases"],
                "limitations":quote["limitations"]}
    if quote.get("planning_brief"):
        manifest["planning_brief"] = deepcopy(quote["planning_brief"])
        manifest["planning_brief_hash"] = quote["planning_brief"]["content_hash"]
        manifest["constraint_assessment"] = check_constraints(scenario, quote["planning_brief"])
    return {"id":new_id("run"), "project_id":project, "scenario_id":scenario["id"],
            "status":"queued", "stage":"queued", "progress":0, "created_at":utc_now(),
            "manifest":manifest, "result":None, "error":None, "cancel_requested":False,
            "attempt":attempt, "retry_of":retry_of}


def qualification_records():
    root = Path(__file__).resolve().parents[1]
    records = []
    for name, status in [("validation/numerical-qualification.json", "mesh_concentration_sensitivity_failed"),
                         ("validation/aij-case-a-diagnostic.json", "urban_method_admission_failed")]:
        path = root/name
        if path.is_file():
            records.append({"path":name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                            "status":status,"independent_signoff":False})
    return records


def compare(store, project_id, body, identity):
    runs = [project_resource(required_run(store, identifier, identity), project_id)
            for identifier in [body.baseline_run_id, body.alternative_run_id]]
    for run in runs:
        reject_inventory_preview(run.get("manifest",{}).get("inputs"))
    if any(run["status"] != "complete_unqualified" for run in runs):
        error("RUN_INCOMPLETE", "Comparison requires two committed, verified research-preview runs", 409)
    manifests = [run["manifest"] for run in runs]
    reasons = []
    for key in ["species", "tier", "model_version", "science_build_hash", "dependency_versions", "conditions"]:
        if manifests[0][key] != manifests[1][key]:
            reasons.append({"field":key, "message":"Frozen values differ"})
    if manifests[0].get("planning_brief_hash") != manifests[1].get("planning_brief_hash"):
        reasons.append({"field":"planning_brief", "message":"Planning requirements differ between studies"})
    def receptor_support(manifest):
        scenario = manifest["inputs"]
        return {"origin":scenario["origin"], "coordinate_frame":scenario.get("coordinate_frame"),
                "analysis_domain":scenario.get("analysis_domain"),
                "receptors":sorted([{k:r.get(k) for k in ["id", "x", "y", "z", "protected"]}
                                    for r in scenario["receptors"]], key=lambda r:r["id"])}
    if receptor_support(manifests[0]) != receptor_support(manifests[1]):
        reasons.append({"field":"receptors", "message":"Location, height, coordinate frame or protected status differs"})
    if reasons:
        error("COMPARISON_UNPAIRED", "Runs do not share scientific comparison support", 409, reasons)
    outputs = [read_run_bundle(store, run)["result"] for run in runs]
    first = {m["receptor_id"]:m for m in outputs[0]["metrics"]}
    second = {m["receptor_id"]:m for m in outputs[1]["metrics"]}
    if first.keys() != second.keys():
        error("COMPARISON_UNPAIRED", "Result receptor identities differ", 409)
    protected = {r["id"]:r.get("protected", False) for r in manifests[0]["inputs"]["receptors"]}
    metrics = []
    for identifier, base in first.items():
        alt = second[identifier]
        if base.get("sampling") != alt.get("sampling") or base.get("metric_definition") != alt.get("metric_definition"):
            error("COMPARISON_UNPAIRED", "Receptor sampling policy or metric definition differs", 409)
        valid = base.get("valid") and alt.get("valid")
        b, a = (base.get("value_ug_m3"), alt.get("value_ug_m3")) if valid else (None, None)
        delta = a-b if valid and a is not None and b is not None else None
        # Presentation denominator floor for a two-decimal display, not a sensor/health threshold.
        percent = 100*delta/b if delta is not None and abs(b) >= 0.005 else None
        metrics.append({"receptor_id":identifier, "name":base["name"], "baseline_ug_m3":b,
                        "alternative_ug_m3":a, "delta_ug_m3":delta, "percent_change":percent,
                        "protected":protected[identifier], "valid":bool(valid),
                        "requested_height_m":base.get("requested_height_m"),
                        "baseline_cell_centre":base.get("cell_centre"),
                        "alternative_cell_centre":alt.get("cell_centre"),
                        "sampling":base.get("sampling"),"metric_definition":base.get("metric_definition"),
                        "percent_suppression_reason":None if percent is not None else "Invalid or baseline below 0.005 ug/m3 presentation floor; inspect absolute delta",
                        "reason":None if valid else "One or both receptor samples are invalid"})
    return {"baseline_run_id":runs[0]["id"], "alternative_run_id":runs[1]["id"],
            "metrics":metrics, "species":"passive_tracer", "units":"ug/m3",
            "qualification":"research_preview", "decision_adequacy":"not_established",
            "limitations":list(dict.fromkeys(outputs[0]["limitations"]+outputs[1]["limitations"]))}


def reference_hashes():
    root = Path(__file__).resolve().parents[1]
    files = ["output/reference/Urban_Air_Studio_Blueprint.md", "output/reference/Urban_Air_Studio_Blueprint.pdf",
             "output/Urban_Air_Studio_Execution_Guide.md", "contracts/phase-a-interface.md"]
    return {name:"sha256:"+hashlib.sha256((root/name).read_bytes()).hexdigest()
            for name in files if (root/name).is_file()}


def checked_report(store, report, identity):
    original = {k:v for k,v in report.items() if not k.startswith("_") and k != "checksum"}
    if digest(original) != report.get("checksum"):
        error("REPORT_INTEGRITY_ERROR", "Report contents no longer match their frozen identity", 503)
    if report.get("_html_sha256") and hashlib.sha256(report["_html"].encode("utf-8")).hexdigest() != report["_html_sha256"]:
        error("REPORT_INTEGRITY_ERROR", "Report document failed integrity checks", 503)
    from api.research_export import admitted_package
    for manifest in report.get("manifest",{}).get("run_manifests",[]):
        inputs = manifest["inputs"]
        if inputs["project_id"] != report["project_id"]:
            error("REPORT_INTEGRITY_ERROR", "Report source belongs to another project", 503)
        admitted_package(store, inputs, identity, error)
    return report


def report_html(report, outputs):
    def esc(value):
        return html.escape(str(value))
    def value(number):
        return "No data" if number is None else f"{number:.6g}"
    def native_height(centre):
        return value(centre[2]) if isinstance(centre,list) and len(centre) == 3 else "Unavailable"
    rows = "".join("<tr>"+"".join(f"<td>{esc(cell)}</td>" for cell in
                    [m["name"], f"Requested {value(m.get('requested_height_m'))} m → baseline {native_height(m.get('baseline_cell_centre'))} m / alternative {native_height(m.get('alternative_cell_centre'))} m native cell centre",
                     value(m["baseline_ug_m3"]), value(m["alternative_ug_m3"]), value(m["delta_ug_m3"]),
                     "Suppressed" if m["percent_change"] is None else value(m["percent_change"])+"%",
                     "Yes" if m["protected"] else "No"])+"</tr>" for m in report["comparison"]["metrics"])
    values = [v for output in outputs for v in output["field"]["values"] if v is not None]
    bound = max(values, default=1) or 1
    maps = []
    for label, output in zip(["Baseline", "Alternative"], outputs):
        field = output["field"]
        cells = []
        for index, v in enumerate(field["values"]):
            color = "#cbd5e1" if v is None else f"hsl({240-210*min(1,max(0,v/bound)):.2f},65%,55%)"
            cells.append(f'<rect x="{index%field["nx"]}" y="{field["ny"]-1-index//field["nx"]}" width="1" height="1" fill="{color}"/>')
        maps.append(f'<figure><figcaption>{label}; slice {field["height_m"]:.3g} m AGL; same scale 0–{bound:.4g} ug/m³</figcaption><svg role="img" aria-label="{label} concentration slice" viewBox="0 0 {field["nx"]} {field["ny"]}">{"".join(cells)}</svg></figure>')
    manifest = report["manifest"]
    run_manifests = manifest["run_manifests"]
    conditions = run_manifests[0]["conditions"]
    metrics = report["comparison"]["metrics"]
    worsened = [m for m in metrics if m["protected"] and m["delta_ug_m3"] is not None and m["delta_ug_m3"] > 0]
    neighbour_notice = ("Protected locations with a positive preview delta: " + ", ".join(m["name"] for m in worsened)) if worsened else "No positive delta at the valid protected locations in this preview. Physical no-harm is not established."
    assessments = [r.get("constraint_assessment") for r in run_manifests]
    constraint_text = "Declared geometry requirements: " + " / ".join(a["status"] if a else "not configured" for a in assessments) + ". This is not a planning-code or environmental safety certification."
    changed = []
    for group in ("buildings", "sources", "receptors"):
        before = {v["id"]:v for v in run_manifests[0]["inputs"][group]}
        after = {v["id"]:v for v in run_manifests[1]["inputs"][group]}
        for identifier in sorted(set(before) | set(after)):
            if before.get(identifier) != after.get(identifier):
                keys = [key for key in set((before.get(identifier) or {})) | set((after.get(identifier) or {})) if (before.get(identifier) or {}).get(key) != (after.get(identifier) or {}).get(key)]
                changed.append(f"{group[:-1].capitalize()} {identifier}: {', '.join(sorted(keys))}")
    source_packages = manifest.get("source_export_packages", [])
    source_notices = "".join(f"<p>{esc(p['source_compliance']['notice'])}</p>" for p in source_packages if p.get("source_compliance"))
    if source_notices:
        source_notices += '<p>ODbL notices and machine-readable original objects, derivative inputs and alterations are included in the reproduction appendix and JSON report.</p>'
    fonts_dir = Path(__file__).resolve().parents[1] / "web/public/fonts"
    font_css = ""
    for family, filename, weight in [("Inter", "inter-latin-variable.woff2", "100 900"), ("Poppins", "poppins-latin-600.woff2", "600")]:
        path = fonts_dir / filename
        if path.is_file():
            font_css += f"@font-face{{font-family:{family};font-style:normal;font-weight:{weight};src:url(data:font/woff2;base64,{base64.b64encode(path.read_bytes()).decode('ascii')}) format('woff2')}}"
    font_licences = "\n\n".join((fonts_dir / f).read_text(encoding="utf-8") for f in ["inter-OFL.txt", "poppins-OFL.txt"] if (fonts_dir / f).is_file())
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>{esc(report["title"])}</title>
<style>{font_css}body{{font:16px/1.5 Inter,system-ui;color:#172033;max-width:1100px;margin:32px auto;padding:24px}}h1,h2{{font-family:Poppins,system-ui;font-weight:600}}h1{{font-size:28px}}h2{{font-size:20px;margin-top:28px}}.notice{{background:#fff3cf;padding:16px;border-left:4px solid #c88c1b}}.neighbours{{padding:16px;border:1px solid #c88c1b;border-radius:8px}}table{{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}}.table-scroll{{overflow-x:auto}}td,th{{border:1px solid #cbd5e1;padding:10px;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace}}summary{{cursor:pointer;font-weight:600;padding:12px 0}}.maps{{display:flex;gap:20px}}figure{{width:48%;margin:0}}svg{{width:100%;background:#eef2f6}}@media(max-width:700px){{.maps{{display:block}}figure{{width:100%;margin-bottom:20px}}body{{margin:0;padding:16px}}}}@media print{{body{{margin:0}}details{{break-before:page}}}}</style>
<h1>{esc(report["title"])}</h1><p>Draft · {esc(report["created_at"])} · {esc(report["id"])}</p>
<div class="notice"><strong>Executed research preview — no site validation or decision qualification.</strong> Passive tracer, ug/m³; conditional end-of-run concentration, not regulatory AQI or measured air quality. Geometry and sources are user-supplied/unverified.</div>
<h2>Planning question</h2><p>{esc(manifest.get('planning_question') or 'Compare the retained baseline with the declared alternative under the same study conditions.')}</p>
<h2>What changed</h2><ul>{''.join(f'<li>{esc(item)}</li>' for item in changed) or '<li>No physical input change in this pair.</li>'}</ul>
<h2>Conditions and feasibility</h2><p>Wind {value(conditions['wind_speed_m_s'])} m/s from {value(conditions['wind_from_deg'])}°; background {value(conditions['background_ug_m3'])} ug/m³; duration {value(conditions['duration_s'])} s. These are declared conditions, not a measured site forecast.</p><p>{esc(constraint_text)}</p>
<div class="neighbours"><strong>Neighbour check</strong><p>{esc(neighbour_notice)}</p></div>
<h2>Decision status: inconclusive for real-world benefit</h2><p>The executed comparison can support further investigation. The method and default mesh are not qualified to establish a real exposure reduction or approve a design. Obtain independent numerical and site evidence before using the preview for a planning decision.</p>
<h2>Exact comparison</h2><p>Alternative minus baseline; positive values worsen concentration. No health or real-world reduction claim is supported.</p>
<p>Temporal support: end state at {esc(report['manifest']['run_manifests'][0]['conditions']['duration_s'])} seconds after start; no temporal averaging. Receptors sample the piecewise constant value of the containing native 3D cell. Requested height and actual cell-centre height are shown separately.</p>
<div class="table-scroll"><table><thead><tr><th>Assessment location</th><th>Requested → native height</th><th>Baseline ug/m³</th><th>Alternative ug/m³</th><th>Signed delta ug/m³</th><th>Change</th><th>Protected</th></tr></thead><tbody>{rows}</tbody></table></div>
<p>Percentages are suppressed for invalid samples or baselines below 0.005 ug/m³, a presentation denominator floor for a two-decimal display. This is not a detection limit or health threshold; absolute deltas remain available.</p>
<h2>Fixed-scale fields</h2><div class="maps">{"".join(maps)}</div><p>Grey cells are masked/no data; rendering is separate from authoritative receptor samples.</p>
<h2>Limits and unresolved evidence</h2><ul>{"".join(f"<li>{esc(item)}</li>" for item in report["limitations"])}</ul>
<h2>Source permissions and attribution</h2>{source_notices or '<p>Permitted user-supplied research inputs. Their declared permissions are retained with the frozen source records.</p>'}
<details><summary>Methods, frozen inputs, provenance and reproducibility</summary><pre>{esc(json.dumps(report["manifest"],indent=2,ensure_ascii=False))}</pre></details>
<details><summary>Bundled typeface licences</summary><pre>{esc(font_licences)}</pre></details>
<p>Report content checksum: {esc(report["checksum"])}</p></html>'''


def create_app(directory=None):
    app = FastAPI(title="Urban Air Studio — Phase A API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.readiness = Readiness()
    if directory:
        app.state.store = Store(directory)

    @app.middleware("http")
    async def local_limits(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        authorization = request.headers.get("authorization", "").split()
        if (len(authorization) == 2 and authorization[0].lower() == "bearer"
                and authorization[1].lower().startswith("uarv1_")
                and request.url.path not in {"/api/review/report", "/api/review/report/comments", "/api/review/report/download"}):
            return JSONResponse(status_code=403, content={"detail": {
                "code": "REVIEW_SCOPE_DENIED",
                "message": "This review link only permits its frozen report and review comments"}},
                headers={"Cache-Control": "no-store", "X-Request-ID": request.state.request_id,
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 5_000_000):
            return JSONResponse(status_code=413, content={"detail":{"code":"IMPORT_LIMIT_EXCEEDED", "message":"Request exceeds 5 MB local allowance"}})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; frame-ancestors 'none'" if request.url.path.endswith("/download") else "frame-ancestors 'none'"
        return response

    @app.exception_handler(Exception)
    async def scientific_exception(request, exception):
        from science.geometry import ScienceError
        if isinstance(exception, ScienceError):
            return JSONResponse(status_code=422, content={"detail":{"code":exception.code,"message":exception.message,"issues":exception.issues}})
        # Unexpected exceptions are logged by the server and never expose private paths.
        route = request.scope.get("route")
        logging.getLogger("urban_air.api").error(json.dumps({"event":"service_failure","exception_type":type(exception).__name__,
            "route":getattr(route,"path","unresolved"),"request_id":getattr(request.state,"request_id",None)}))
        return JSONResponse(status_code=500, content={"detail":{"code":"INTERNAL_ERROR","message":"Unexpected local service failure; inspect operator logs"}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exception):
        # Do not echo private input payloads or non-finite values into JSON error output.
        issues = [{"location":list(item["loc"]),"type":item["type"],"message":item["msg"]} for item in exception.errors()]
        return JSONResponse(status_code=422,content={"detail":{"code":"INPUT_VALIDATION_FAILED", "message":"Inputs failed finite/unit/bounds validation", "issues":issues}})

    @app.get("/api/health")
    def health():
        return {"status":"ok", "mode":os.getenv("UA_MODE", "local"),
                "identity_configured":public_config()["configured"], "production_ready":False}

    @app.get("/api/ready")
    def ready(request: Request):
        status, payload = request.app.state.readiness.probe(request,get_store)
        return JSONResponse(status_code=status,content=payload)

    @app.get("/api/auth/config")
    def auth_config():
        return public_config()

    @app.get("/api/capabilities")
    def capabilities(identity=Depends(local_identity), store=Depends(get_store)):
        from science import MODEL_VERSION
        from api.assistant import LocalModel
        assistant = LocalModel().status()
        return {"phase":"A/B engineering; C/D deadline increments", "model_version":MODEL_VERSION, "supported_tiers":["T0","T1"],
                "capability_statuses":{"T0":"implemented_geometry_diagnostics", "T1":"research_preview_unvalidated", "T2":"native_diagnostics_not_qualified_for_decision_comparison"},
                "species":["passive_tracer"], "imports":["GeoJSON Polygon/Point"],
                "identity":os.getenv("UA_MODE", "local"), "storage":store.adapter_name,
                "operator_views_available":os.getenv("UA_MODE", "local") == "local",
                "ai_available":assistant["state"] in {"ready", "busy"},
                "study_assistant":{"state":assistant["state"], "scope":"reviewed local study tools", "qualified":False},
                "cloud_deployed":False, "validated":False,
                "geographic_context":{"map_navigation":"Delhi/India and supported worldwide coordinates", "providers":["OSM geometry", "Open-Meteo GFS weather context"], "semantic_preparation":"review_required", "national_scientific_coverage":False},
                "limitations":["Bounded research preview, not urban CFD validation", "Context coverage does not establish source permissions, measured inputs or scientific qualification", "Native numerical/scientific qualification remains incomplete"]}

    @app.get("/api/fixtures/campus")
    def fixture(identity=Depends(local_identity)):
        from science.fixtures import load_fixture
        return load_fixture()

    @app.get("/api/projects",response_model=list[response.Project])
    def projects(identity=Depends(local_identity), store=Depends(get_store)):
        return store.list("project", identity.tenant_id)

    @app.post("/api/projects", status_code=201,response_model=response.Project)
    def project_create(body: ProjectCreate, identity=Depends(local_identity), store=Depends(get_store)):
        project = {"id":new_id("project"), "name":body.name, "created_at":utc_now(), "baseline_scenario_id":None,
                   "category":body.category, "planning_question":body.planning_question,
                   "site":body.site.model_dump() if body.site else None,"planning_brief_id":None}
        with store.transaction() as db:
            Store.put(db, "project", identity.tenant_id, project)
        return project

    @app.get("/api/projects/{project_id}",response_model=response.ProjectDetail)
    def project_get(project_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        project = required(store, "project", project_id, identity)
        return {"project":project, "scenarios":store.list("scenario", identity.tenant_id, project_id),
                "runs":[public_run(store, run) for run in store.list_runs(identity.tenant_id, project_id)],
                "imports":store.list("import", identity.tenant_id, project_id),
                "reports":[{k:v for k,v in checked_report(store, report, identity).items() if not k.startswith("_")} for report in store.list("report",identity.tenant_id,project_id)]}

    @app.post("/api/projects/{project_id}/imports", status_code=201,response_model=response.ImportResponse)
    def import_create(project_id: str, body: ImportRequest, identity=Depends(local_identity), store=Depends(get_store)):
        from science.geometry import import_geojson
        required(store, "project", project_id, identity)
        if not body.rights.derive:
            error("LICENCE_USE_BLOCKED", "Geometry derivation permission is required", 403)
        try:
            encoded_geojson = canonical(body.geojson).encode("utf-8")
        except (ValueError, TypeError):
            error("INPUT_VALIDATION_FAILED", "GeoJSON must contain finite JSON values", 422)
        if len(encoded_geojson) > 4_000_000:
            error("IMPORT_LIMIT_EXCEEDED", "GeoJSON exceeds 4 MB parsing allowance", 413)
        core, audit = scientific_call(import_geojson, body.geojson, body.rights.model_dump(), body.source_label)
        core.update(source_rights=body.rights.model_dump(), source_label=body.source_label)
        scenario = scenario_version(core, project_id, f"Imported {body.filename}"[:120])
        record = {"id":new_id("import"), "project_id":project_id, "filename":body.filename,
                  "source_label":body.source_label, "rights":body.rights.model_dump(), "created_at":utc_now(),
                  "scenario_id":scenario["id"], "audit":audit, "raw_checksum":digest(body.geojson)}
        with store.transaction() as db:
            Store.put(db, "scenario", identity.tenant_id, scenario, project_id)
            Store.put(db, "import", identity.tenant_id, record, project_id)
        return {"id":record["id"], "scenario":scenario, "audit":audit}

    @app.post("/api/projects/{project_id}/baseline",response_model=response.Project)
    def baseline_set(project_id: str, body: BaselineRequest, identity=Depends(local_identity), store=Depends(get_store)):
        with store.transaction() as db:
            project = required(store, "project", project_id, identity, db)
            scenario = project_resource(required(store, "scenario", body.scenario_id, identity, db), project_id)
            require_scenario_permission(store, scenario, identity, "derive", db)
            if not scenario.get("diagnostics"):
                error("GEOMETRY_NOT_AUDITED", "An audited snapshot is required")
            project["baseline_scenario_id"] = scenario["id"]
            db.execute("UPDATE objects SET data=? WHERE kind='project' AND id=? AND tenant_id=?",
                       (canonical(project), project_id, identity.tenant_id))
        return project

    @app.get("/api/scenarios/{scenario_id}",response_model=response.Scenario)
    def scenario_get(scenario_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        return required(store, "scenario", scenario_id, identity)

    @app.post("/api/scenarios/{scenario_id}/branch", status_code=201,response_model=response.Scenario)
    def branch(scenario_id: str, body: Named, identity=Depends(local_identity), store=Depends(get_store)):
        parent = required(store, "scenario", scenario_id, identity)
        require_scenario_permission(store, parent, identity, "derive")
        result = scenario_version(parent, parent["project_id"], body.name, parent)
        with store.transaction() as db:
            Store.put(db, "scenario", identity.tenant_id, result, parent["project_id"])
        return result

    @app.post("/api/scenarios/{scenario_id}/operations", status_code=201,response_model=response.Scenario)
    def operations(scenario_id: str, body: OperationsRequest, identity=Depends(local_identity), store=Depends(get_store)):
        from science.geometry import validate_scenario
        parent = required(store, "scenario", scenario_id, identity)
        require_scenario_permission(store, parent, identity, "derive")
        if parent["version"] != body.expected_version:
            error("VERSION_CONFLICT", "Expected version does not match immutable snapshot", 409)
        core = deepcopy(parent)
        for operation in body.operations:
            group = {"building_update":"buildings", "source_update":"sources", "receptor_update":"receptors"}[operation.type]
            obj = next((obj for obj in core[group] if obj["id"] == operation.object_id), None)
            if obj is None:
                error("OBJECT_NOT_FOUND", "Operation object is absent from this snapshot", 404)
            changes = operation.changes.model_dump(exclude_none=True)
            if not changes:
                error("EMPTY_OPERATION", "At least one actual parameter change is required")
            if group == "buildings":
                footprint = obj["footprint"]
                cx, cy = sum(p[0] for p in footprint)/len(footprint), sum(p[1] for p in footprint)/len(footprint)
                theta = math.radians(changes.get("rotation_deg", 0))
                obj["footprint"] = [[cx+(p[0]-cx)*math.cos(theta)-(p[1]-cy)*math.sin(theta)+changes.get("dx",0),
                                     cy+(p[0]-cx)*math.sin(theta)+(p[1]-cy)*math.cos(theta)+changes.get("dy",0)] for p in footprint]
                if "height_m" in changes:
                    obj["height_m"] = changes["height_m"]
            else:
                obj.update(changes)
        core["diagnostics"] = scientific_call(validate_scenario, core)
        brief = latest_brief(store, parent["project_id"], identity)
        if brief:
            assessment = check_constraints(core, brief, parent)
            if not assessment["valid"]:
                error("PLANNING_CONSTRAINT_FAILED", "Alternative violates declared planning requirements", 422, assessment["issues"])
        result = scenario_version(core, parent["project_id"], parent["name"], parent, [op.model_dump(exclude_none=True) for op in body.operations])
        with store.transaction() as db:
            Store.put(db, "scenario", identity.tenant_id, result, parent["project_id"])
        return result

    @app.post("/api/projects/{project_id}/quote", status_code=201,response_model=response.Quote)
    def quote(project_id: str, body: QuoteRequest, identity=Depends(local_identity), store=Depends(get_store)):
        from science.geometry import validate_scenario
        from science.model import make_grid
        required(store, "project", project_id, identity)
        brief = latest_brief(store, project_id, identity)
        grid_sizes = []
        for identifier in body.scenario_ids:
            scenario = project_resource(required(store, "scenario", identifier, identity), project_id)
            require_scenario_permission(store, scenario, identity, "solve")
            reject_inventory_preview(scenario)
            if not scenario.get("source_rights", {}).get("solve"):
                error("LICENCE_USE_BLOCKED", "Solver-use permission is required", 403)
            scientific_call(validate_scenario, scenario)
            if brief:
                assessment = check_constraints(scenario, brief)
                if not assessment["valid"]:
                    error("PLANNING_CONSTRAINT_FAILED", "Study inputs violate declared planning requirements", 422, assessment["issues"])
            grid = scientific_call(make_grid, scenario)
            grid_sizes.append({"scenario_id":identifier,"nx":grid.nx,"ny":grid.ny,"nz":grid.nz,"cells":grid.nx*grid.ny*grid.nz})
        result = {"id":new_id("quote"), "project_id":project_id, "scenario_ids":body.scenario_ids,
                  "conditions":body.conditions.model_dump(), "tier":"T1", "cases":len(body.scenario_ids),
                  "maximum_seconds":120*len(body.scenario_ids),
                  "planning_brief":deepcopy(brief),
                  "preflight_grid_sizes":grid_sizes,
                  "expires_at":(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(),
                  "limitations":["Local wall-time allowance, not currency or an empirically calibrated cloud quote",
                                 "Executed passive-tracer research preview; no atmospheric turbulence or observational validation",
                                 "Default mesh is not convergence-qualified; design differences may reflect grid and source/receptor sampling effects",
                                 "Independent AIJ airflow diagnostic failed method admission and accuracy targets; this worker is not a qualified urban predictor",
                                 "End-of-run concentration under fixed conditions; not regulatory AQI or a health outcome"]}
        with store.transaction() as db:
            Store.put(db, "quote", identity.tenant_id, result, project_id)
        return result

    @app.post("/api/projects/{project_id}/runs", status_code=202,response_model=response.RunSubmission)
    def submit(project_id: str, body: RunRequest, identity=Depends(local_identity), store=Depends(get_store)):
        request_hash = digest({"quote_id":body.quote_id})
        with store.transaction() as db:
            required(store, "project", project_id, identity, db)
            prior = db.execute("SELECT * FROM submissions WHERE tenant_id=? AND project_id=? AND key=?",
                               (identity.tenant_id, project_id, body.idempotency_key)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    error("IDEMPOTENCY_CONFLICT", "Idempotency key was used for a different quote", 409)
                identifiers = json.loads(prior["data"])
                return {"runs":[public_run(store, store.run(i, identity.tenant_id, db)) for i in identifiers]}
            quote = project_resource(required(store, "quote", body.quote_id, identity, db), project_id)
            if datetime.fromisoformat(quote["expires_at"]) < datetime.now(timezone.utc):
                error("QUOTE_EXPIRED", "Request a new local allowance", 409)
            if db.execute("SELECT 1 FROM quote_uses WHERE quote_id=?", (quote["id"],)).fetchone():
                error("QUOTE_CONSUMED", "Quote already submitted; recover the original submission", 409)
            active = db.execute("SELECT COUNT(*) FROM runs WHERE tenant_id=? AND status IN ('queued','preparing','solving','verifying')",
                                (identity.tenant_id,)).fetchone()[0]
            if active+quote["cases"] > 16:
                error("BUDGET_EXCEEDED", "Local tenant allowance is 16 active cases", 409)
            inputs = [project_resource(required(store,"scenario",i,identity,db),project_id) for i in quote["scenario_ids"]]
            for scenario in inputs:
                require_scenario_permission(store, scenario, identity, "solve", db)
            runs = [new_run(project_id, scenario, quote) for scenario in inputs]
            for run in runs:
                Store.insert_run(db, identity.tenant_id, run)
            db.execute("INSERT INTO submissions VALUES(?,?,?,?,?)", (identity.tenant_id,project_id,body.idempotency_key,request_hash,canonical([r["id"] for r in runs])))
            db.execute("INSERT INTO quote_uses VALUES(?,?)", (quote["id"], body.idempotency_key))
        return {"runs":runs}

    @app.get("/api/runs/{run_id}",response_model=response.Run)
    def run_get(run_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        return public_run(store, required_run(store, run_id, identity))

    @app.get("/api/runs/{run_id}/result",response_model=response.Result)
    def run_result(run_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        run = required_run(store, run_id, identity)
        if run["status"] != "complete_unqualified":
            error("RUN_INCOMPLETE", "No committed verified result is available", 409)
        return public_run(store, run)["result"]

    @app.post("/api/runs/{run_id}/cancel",response_model=response.Run)
    def cancel(run_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        with store.transaction() as db:
            run = store.run(run_id, identity.tenant_id, db)
            if not run:
                error("NOT_FOUND", "Run not found", 404)
            if run["status"] in TERMINAL:
                return public_run(store, run)
            db.execute("UPDATE runs SET cancel_requested=1 WHERE id=? AND tenant_id=?", (run_id, identity.tenant_id))
            if run["status"] == "queued":
                run.update(stage="cancelled", finished_at=utc_now(), resource_use={"worker_seconds":0,"billed_currency":None})
                clean = {k:v for k,v in run.items() if not k.startswith("_")}
                db.execute("UPDATE runs SET status='cancelled',data=? WHERE id=?", (canonical(clean),run_id))
            run = store.run(run_id, identity.tenant_id, db)
        return public_run(store, run)

    @app.post("/api/runs/{run_id}/retry", status_code=202,response_model=response.Run)
    def retry(run_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        with store.transaction() as db:
            old = store.run(run_id, identity.tenant_id, db)
            if not old:
                error("NOT_FOUND", "Run not found", 404)
            reject_inventory_preview(old.get("manifest",{}).get("inputs"))
            if old.get("retried_run_id"):
                return public_run(store, store.run(old["retried_run_id"], identity.tenant_id, db))
            if old["status"] not in {"failed","cancelled"}:
                error("RETRY_NOT_ALLOWED", "Only cancelled or infrastructure-failed runs can retry", 409)
            if old.get("error") and old["error"]["code"] not in {"WORKER_LOST", "WORKER_INTERRUPTED", "LOCAL_IO_ERROR"}:
                error("RETRY_NOT_ALLOWED", "Scientific/input failure requires diagnosis and a new experiment", 409)
            if old["attempt"] >= 3:
                error("BUDGET_EXCEEDED", "Maximum three attempts; request a new reviewed allowance", 409)
            active = db.execute("SELECT COUNT(*) FROM runs WHERE tenant_id=? AND status IN ('queued','preparing','solving','verifying')",
                                (identity.tenant_id,)).fetchone()[0]
            if active >= 16:
                error("BUDGET_EXCEEDED", "Local tenant allowance is 16 active cases, including retries", 409)
            new = {k:deepcopy(v) for k,v in old.items() if not k.startswith("_")}
            new.update(id=new_id("run"),status="queued",stage="queued",progress=0,created_at=utc_now(),
                       cancel_requested=False,error=None,result=None,attempt=old["attempt"]+1,retry_of=old["id"])
            for key in ["finished_at","resource_use","retried_run_id"]:
                new.pop(key,None)
            Store.insert_run(db,identity.tenant_id,new)
            old["retried_run_id"] = new["id"]
            db.execute("UPDATE runs SET data=? WHERE id=?", (canonical({k:v for k,v in old.items() if not k.startswith("_")}),old["id"]))
        return new

    @app.post("/api/projects/{project_id}/compare",response_model=response.Comparison)
    def comparison(project_id: str, body: ComparisonRequest, identity=Depends(local_identity), store=Depends(get_store)):
        required(store,"project",project_id,identity)
        return compare(store,project_id,body,identity)

    @app.post("/api/projects/{project_id}/reports", status_code=201,response_model=response.Report)
    def report_create(project_id: str, body: ReportRequest, identity=Depends(local_identity), store=Depends(get_store)):
        project = required(store,"project",project_id,identity)
        comparison = compare(store,project_id,body,identity)
        runs = [required_run(store,i,identity) for i in [body.baseline_run_id,body.alternative_run_id]]
        from api.research_export import admitted_package
        exports = [admitted_package(store, r["manifest"]["inputs"], identity, error) for r in runs]
        outputs = [read_run_bundle(store, run) for run in runs]
        report = {"id":new_id("report"),"project_id":project_id,"title":body.title,"status":"draft", "created_at":utc_now(),
                  "baseline_run_id":body.baseline_run_id,"alternative_run_id":body.alternative_run_id,"comparison":comparison,
                  "limitations":comparison["limitations"],"manifest":{"schema_version":"phase-a-report-v1",
                    "run_manifests":[r["manifest"] for r in runs],"artifact_manifests":[o["artifact_manifest"] for o in outputs],
                    "reference_hashes":reference_hashes(),"approval":"Not reviewed or scientifically validated",
                    "planning_question":runs[0]["manifest"].get("planning_brief",{}).get("requirements",{}).get("question") or project.get("planning_question"),
                    "project_name":project["name"], "source_export_packages":exports,
                    "omissions":["No independent site measurements","No field-season validation","No uncertainty ensemble",
                                 "No planning feasibility certification","No health inference"]}}
        report["checksum"] = digest(report)
        report["_html"] = report_html(report, [o["result"] for o in outputs])
        report["_html_sha256"] = hashlib.sha256(report["_html"].encode("utf-8")).hexdigest()
        with store.transaction() as db:
            Store.put(db,"report",identity.tenant_id,report,project_id)
        return {k:v for k,v in report.items() if not k.startswith("_")}

    @app.get("/api/reports/{report_id}",response_model=response.Report)
    def report_get(report_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        report=checked_report(store, required(store,"report",report_id,identity), identity)
        for manifest in report.get("manifest",{}).get("run_manifests",[]):
            reject_inventory_preview(manifest.get("inputs"))
        return {k:v for k,v in report.items() if not k.startswith("_")}

    @app.get("/api/projects/{project_id}/reports", response_model=list[response.Report])
    def report_list(project_id: str, identity=Depends(local_identity), store=Depends(get_store)):
        required(store, "project", project_id, identity)
        return [{k:v for k,v in checked_report(store, report, identity).items() if not k.startswith("_")}
                for report in store.list("report", identity.tenant_id, project_id)]

    @app.get("/api/reports/{report_id}/download")
    def report_download(report_id: str, format: str="html", identity=Depends(local_identity), store=Depends(get_store)):
        report = checked_report(store, required(store,"report",report_id,identity), identity)
        for manifest in report.get("manifest",{}).get("run_manifests",[]):
            reject_inventory_preview(manifest.get("inputs"))
        headers = {"Content-Disposition":f'attachment; filename="{report_id}.{format if format in {"html","json"} else "txt"}"'}
        if format == "html":
            return HTMLResponse(report["_html"],headers=headers)
        if format == "json":
            return JSONResponse({k:v for k,v in report.items() if not k.startswith("_")},headers=headers)
        error("EXPORT_FORMAT_UNSUPPORTED", "Choose html or json")

    from api.phase_b import make_router, install_openapi_contract
    from api.authorization import Authorizer
    app.state.authorization=Authorizer()
    from api.planning import make_router as planning_router
    app.include_router(planning_router(identity_dependency=local_identity,store_dependency=get_store,
                                       required=required,project_resource=project_resource,error=error))
    app.include_router(make_router(identity_dependency=local_identity,store_dependency=get_store,
                                  required=required,project_resource=project_resource,error=error,
                                  physical_hash=physical_hash,authorizer=app.state.authorization))
    from api.detailed_execution import make_router as detailed_router
    app.include_router(detailed_router(identity_dependency=local_identity,store_dependency=get_store,
                                       required=required,project_resource=project_resource,error=error,
                                       physical_hash=physical_hash,authorizer=app.state.authorization))
    from api.explore import ExploreBroker, make_router as explore_router
    app.state.explore_broker = ExploreBroker()
    app.include_router(explore_router(identity_dependency=local_identity,store_dependency=get_store,
                                     required=required,error=error))
    from api.site_preparation import make_router as preparation_router
    app.include_router(preparation_router(identity_dependency=local_identity,store_dependency=get_store,
                                         required=required,project_resource=project_resource,error=error,
                                         authorizer=app.state.authorization))
    from api.assistant import make_router as assistant_router
    app.include_router(assistant_router(identity_dependency=local_identity,store_dependency=get_store,
                                       required=required,project_resource=project_resource,
                                       authorizer=app.state.authorization,compare=compare,constraint_validator=validate_programme))
    from api.research_export import make_router as export_router
    app.include_router(export_router(identity_dependency=local_identity,store_dependency=get_store,
                                    required=required,error=error,authorizer=app.state.authorization))
    from api.response_study import make_router as response_study_router
    app.include_router(response_study_router(identity_dependency=local_identity,store_dependency=get_store,
                                            required=required,project_resource=project_resource,
                                            physical_hash=physical_hash,scenario_version=scenario_version,
                                            authorizer=app.state.authorization,constraint_validator=validate_programme,
                                            compare=compare))
    from api.report_review import make_router as report_review_router
    app.include_router(report_review_router(identity_dependency=local_identity,store_dependency=get_store,
                                           required=required,error=error,checked_report=checked_report))
    install_openapi_contract(app)
    app.add_middleware(BoundedBodyMiddleware)
    return app


app = create_app()
