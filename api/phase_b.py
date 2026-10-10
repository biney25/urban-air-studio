"""Scoped immutable Phase B preparation; uploads never establish qualification."""
from copy import deepcopy
from contextlib import closing
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path, PurePosixPath
import re
from statistics import median
from typing import Annotated, Literal
import zipfile

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import Field, ValidationError, model_validator

from api.responses import Output
from api.authorization import AuthorizationMethod
from api.schemas import Identifier, Input, Name, Rights
from api.storage import Store, canonical, digest, new_id, utc_now
from evidence.schemas import EvidenceRequest, Inventory, MeasurementCampaign, ReplayProtocol, ReplayAssessment
from evidence.processing import process_evidence, validate_protocol_campaign, assess_replay

ROOT = Path(__file__).resolve().parents[1]
MAX_RECORDS = 100
MAX_CASE_FILES = 1000
MAX_CASE_BYTES = 32_000_000
MAX_RECORD_BYTES = 8_000_000
EVIDENCE_KIND = "phase_b_evidence"
CASE_KIND = "phase_b_case"
ALLOCATION_KIND = "phase_b_source_allocation"


class EvidenceRecord(Output):
    schema_version: Literal["phase-b-evidence-v1"]
    id: str
    project_id: str
    title: str
    kind: Literal["inventory","measurement_campaign","replay_protocol","replay_assessment"]
    evidence_kind: Literal["observed","provider-modelled","simulated","illustrative"]
    effective_evidence_kind: Literal["observed","provider-modelled","simulated","illustrative"]
    source_label: str
    rights: Rights
    effective_rights: Rights
    payload: dict
    summary: dict
    dependencies: list[dict]
    created_at: str
    content_hash: str
    download_url: str


class DetailedCaseRecord(Output):
    schema_version: Literal["phase-b-case-v1"]
    id: str
    project_id: str
    scenario_id: str
    scenario_version: int
    scenario_hash: str
    title: str
    created_at: str
    rights: Rights
    manifest: dict
    file_count: int
    file_bytes: int
    bundle_checksum: str
    content_hash: str
    status: Literal["prepared_unexecuted"]
    qualified: Literal[False]
    download_url: str


class ReadinessRecord(Output):
    schema_version: Literal["phase-b-readiness-v1"]
    project_id: str
    status: Literal["unqualified"]
    detailed_method: dict
    authorization_method: AuthorizationMethod
    research_preview: dict
    packages: list[dict]
    evidence: list[dict]
    cases: list[dict]
    local_resource_observations: dict
    native_runtime: dict
    native_executions: list[dict]
    scientific_requirements: dict
    scientific_qualification: dict
    review_packet_url: str
    native_package_observations: dict
    source_allocations: list[dict]


class SourceAllocationRecord(Output):
    schema_version: Literal["phase-b-source-allocation-record-v1"]
    id: str
    project_id: str
    title: str
    created_at: str
    parent_scenario_id: str
    parent_scenario_hash: str
    parent_snapshot_hash: str
    scenario_id: str
    scenario_hash: str
    inventory_evidence_id: str
    inventory_content_hash: str
    effective_rights: Rights
    rights: Rights
    effective_evidence_kind: Literal["observed","provider-modelled","simulated","illustrative"]
    allocation_build_hash: str
    plan_hash: str
    artifact_checksum: str
    summary: dict
    status: Literal["allocated_unexecuted"]
    qualified: Literal[False]
    content_hash: str
    download_url: str


def public_allocation(record):
    result={key:deepcopy(value) for key,value in record.items() if not key.startswith("_")}
    result["download_url"]=f"/api/projects/{record['project_id']}/source-allocations/{record['id']}/download"
    return result


def allocation_summary(plan):
    summary={key:deepcopy(plan[key]) for key in ("species","interval_start_utc","interval_end_utc","total_rate_g_s",
        "total_rate_kg_s","total_range_g_s","background_ug_m3","background_mode","native_source_assignment_verified",
        "mass_allocation_arithmetic_verified","limitations")}
    summary.update(source_count=len(plan["sources"]),range_basis="declared_parameter_bounds_not_probability_or_confidence_interval",qualified=False)
    return summary


def scoped_allocation(store,identifier,tenant,project,error,db=None):
    def lookup(connection):
        return connection.execute("SELECT project_id,data FROM objects WHERE kind=? AND id=? AND tenant_id=?",
                                  (ALLOCATION_KIND,identifier,tenant)).fetchone()
    if db is None:
        with closing(store.connect()) as connection:
            row=lookup(connection)
    else:
        row=lookup(db)
    if row is None or row[0]!=project:
        error("NOT_FOUND","Source allocation not found in this project/tenant",404)
    try:
        record=json.loads(row[1])
        if (record.get("id")!=identifier or record.get("project_id")!=project
                or not re.fullmatch(r"allocation_[0-9a-f]{32}",identifier)
                or record.get("content_hash")!=digest(logical_record(record))
                or record.get("rights")!=record.get("effective_rights")):
            raise ValueError("Allocation row binding changed")
        SourceAllocationRecord.model_validate(public_allocation(record))
        if len(canonical(record).encode("utf-8"))>MAX_RECORD_BYTES:
            raise ValueError("Allocation metadata exceeds bounded support")
        return record
    except (ValueError,TypeError,KeyError,AttributeError):
        error("ARTIFACT_INTEGRITY_ERROR","Immutable source allocation scope/checksum could not be verified",503)


def allocation_dependencies(store,scenario,tenant,error,*,config=None,db=None,require=None,current_method=False,
                            allow_rederive=False,_visited=None):
    """Resolve current source authority; uploads cannot establish this binding.

    Permission callbacks run on every current dependency before frozen-content
    checks, and can participate in the worker's publication transaction.
    """
    marker=scenario.get("source_allocation")
    if marker is None:
        return []
    visited=set() if _visited is None else _visited
    project=scenario["project_id"]
    try:
        identifier=marker["allocation_id"]
        if (marker.get("schema_version")!="phase-b-source-allocation-binding-v1"
                or set(marker)!={"schema_version","allocation_id","allocation_content_hash"}):
            raise ValueError("Allocation marker is not the controlled binding schema")
        if identifier in visited or len(visited)>=10:
            raise ValueError("Allocation ancestry is cyclic or exceeds bounded support")
        visited.add(identifier)
        record=scoped_allocation(store,identifier,tenant,project,error,db)
        inventory=store.get(EVIDENCE_KIND,record["inventory_evidence_id"],tenant,db)
        parent=store.get("scenario",record["parent_scenario_id"],tenant,db)
        if inventory is None or parent is None or parent.get("project_id")!=project:
            raise ValueError("Allocation dependency is absent or outside its project")
        verify_record(inventory,error,kind=EVIDENCE_KIND,identifier=record["inventory_evidence_id"],project=project)
        if parent.get("id")!=record["parent_scenario_id"]:
            raise ValueError("Allocation parent differs from authoritative row")
        dependencies=[record,inventory,parent]
        if require is not None:
            for dependency in dependencies:
                require(dependency)
        payload=store.read_artifact(record["_artifact"],record["artifact_checksum"])
        plan=payload["plan"]
        plan_content={key:value for key,value in plan.items() if key!="content_hash"}
        if (marker.get("allocation_content_hash")!=record["content_hash"]
                or plan.get("schema_version")!="phase-b-source-allocation-plan-v1" or plan.get("qualified") is not False
                or plan.get("content_hash")!=digest(plan_content) or plan["content_hash"]!=record["plan_hash"]
                or plan["allocation_build_hash"]!=record["allocation_build_hash"]
                or inventory["kind"]!="inventory" or inventory["content_hash"]!=record["inventory_content_hash"]
                or payload["inventory_payload"]!=inventory["payload"]
                or digest(parent)!=record["parent_snapshot_hash"] or payload["parent_scenario"]!=parent
                or parent["content_hash"]!=record["parent_scenario_hash"]
                or plan["species"]!="passive_tracer" or plan["background_mode"]!="independent_inflow"
                or plan.get("native_source_assignment_verified") is not False
                or plan.get("mass_allocation_arithmetic_verified") is not True
                or record["summary"]!=allocation_summary(plan)
                or digest(plan["sources"])!=plan["source_set_hash"]
                or digest(payload["geometry"])!=plan["geometry_hash"]
                or payload["controls"]!=plan["controls"]):
            raise ValueError("Frozen allocation inventory/geometry/plan authority changed")
        expected_rights={key:parent["source_rights"][key] and inventory["effective_rights"][key] for key in ("derive","solve","export")}
        if record["rights"]!=expected_rights:
            raise ValueError("Allocation rights do not preserve every current dependency")
        if not allow_rederive and (digest(scenario["sources"])!=plan["source_set_hash"]
                or digest(scenario["coordinate_frame"])!=plan["coordinate_frame_hash"]
                or scenario["source_rights"]!=record["rights"]):
            error("ALLOCATION_BINDING_STALE","Sources, permissions or coordinate frame changed; explicitly rederive the inventory allocation",409)
        if config is not None and config.get("background_ug_m3")!=plan["background_ug_m3"]:
            error("ALLOCATION_BACKGROUND_MISMATCH","Detailed background must match the independent inflow frozen in this inventory allocation",422)
        if current_method:
            from evidence.allocation import allocate_inventory,ALLOCATION_BUILD_HASH
            if record["allocation_build_hash"]!=ALLOCATION_BUILD_HASH:
                error("ALLOCATION_METHOD_CHANGED","Source allocator changed; prepare a new explicit allocation",409)
            if allocate_inventory(payload["inventory_payload"],payload["geometry"],payload["parent_scenario"],payload["controls"])!=plan:
                raise ValueError("Allocation differs from the trusted conservative allocator")
            try:
                current_plan=allocate_inventory(payload["inventory_payload"],payload["geometry"],scenario,payload["controls"])
            except (ValueError,ValidationError):
                error("ALLOCATION_BINDING_STALE","Road emission support changed under current geometry/domain; explicitly rederive the allocation",409)
            if current_plan!=plan:
                error("ALLOCATION_BINDING_STALE","Current geometry changes the conservative source plan; explicitly rederive the allocation",409)
        inherited=allocation_dependencies(store,parent,tenant,error,db=db,require=require,allow_rederive=True,
                                           _visited=visited)
        return dependencies+inherited
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        error("ARTIFACT_INTEGRITY_ERROR","Allocation source/interval/background/permission authority could not be verified",503)


PACKAGES = [
    ("B01","Pinned detailed method",["Pinned runtime execution and distribution compatibility","Independent method/process-envelope review"]),
    ("B02","Physical surfaces and mesh",["Executed mesh quality/connectivity and source/receptor assignment","Qualified geometry/domain support"]),
    ("B03","Atmospheric boundary layer",["Actual empty-domain profile compatibility","Relevant independent obstacle-flow benchmark"]),
    ("B04","Conservative scalar transport",["Executed no-source/uniform/manufactured scalar tests","Conservation/positivity and source allocation evidence"]),
    ("B05","Source and background inventories",["Measured/qualified site activity and emission factors","Approved background/local-source treatment and uncertainty"]),
    ("B06","Measurement campaign",["Instrument calibration/collocation and field access","Real deployment, clock/maintenance and campaign review"]),
    ("B07","Numerical sensitivity",["Three detailed mesh levels and domain/closure studies","All relevant metrics meet predeclared sensitivity targets"]),
    ("B08","Held-out replay",["Measured held-out locations/periods and support alignment","Executed replay, uncertainty and regime-level assessment"]),
    ("B09","Qualification and diagnostic workspace",["End-to-end diagnostic/user acceptance","Detailed tier admission only after scientific gates"]),
    ("B10","Paired intervention/no-harm study",["Executed compatible no-change/source-control studies","Paired uncertainty and protected-receptor assessment"]),
    ("B11","Independent scoped review",["Independent reviewer sign-off","Scoped release/envelope with all failures retained"]),
    ("B12","Measured detailed resources",["Representative actual detailed runtime/memory/retry observations","Calibrated detailed cost classes; approved cloud budget if deployed"]),
]


def logical_record(record):
    return {key:value for key,value in record.items() if not key.startswith("_") and key not in {"content_hash","download_url"}}


def verify_record(record, error, *, kind=None, identifier=None, project=None):
    try:
        schema = "phase-b-evidence-v1" if kind == EVIDENCE_KIND else "phase-b-case-v1" if kind == CASE_KIND else None
        if (not isinstance(record,dict) or (identifier is not None and record.get("id") != identifier)
                or (project is not None and record.get("project_id") != project)
                or (schema is not None and record.get("schema_version") != schema)):
            raise ValueError("Immutable object identity/scope changed")
        if record.get("content_hash") != digest(logical_record(record)):
            raise ValueError("Immutable content changed")
        model=EvidenceRecord if record.get("schema_version")=="phase-b-evidence-v1" else DetailedCaseRecord
        model.model_validate(public_record(record))
        prefix="evidence" if model is EvidenceRecord else "dcase"
        if not re.fullmatch(prefix+r"_[0-9a-f]{32}",record["id"]):
            raise ValueError("Immutable identifier is not server generated")
        if len(canonical(record).encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("Immutable record exceeds bounded support")
    except (ValueError,TypeError,AttributeError,KeyError):
        error("ARTIFACT_INTEGRITY_ERROR","Immutable evidence/case failed its stored checksum",503)
    return record


def install_openapi_contract(app):
    """Expose strict conditional payloads while retaining the validated dict seam."""
    base_openapi=app.openapi
    models={"inventory":Inventory,"measurement_campaign":MeasurementCampaign,
            "replay_protocol":ReplayProtocol,"replay_assessment":ReplayAssessment}
    def openapi():
        schema=base_openapi()
        components=schema["components"]["schemas"]
        for model in models.values():
            definition=model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name,nested in definition.pop("$defs",{}).items():
                components.setdefault(name,nested)
            components[model.__name__]=definition
        request=components["EvidenceRequest"]
        request["properties"]["payload"]={"title":"Payload","oneOf":[{"$ref":f"#/components/schemas/{model.__name__}"} for model in models.values()]}
        request["allOf"]=[{"if":{"properties":{"kind":{"const":kind}},"required":["kind"]},
                          "then":{"properties":{"payload":{"$ref":f"#/components/schemas/{model.__name__}"}}}}
                         for kind,model in models.items()]
        request["allOf"][-1]["then"]["properties"]["evidence_kind"]={"enum":["simulated","illustrative"]}
        app.openapi_schema=schema
        return schema
    app.openapi=openapi


def public_record(record):
    result={key:value for key,value in record.items() if not key.startswith("_")}
    segment="evidence" if result["schema_version"] == "phase-b-evidence-v1" else "detailed-cases"
    result["download_url"]=f"/api/projects/{result['project_id']}/{segment}/{result['id']}/download"
    return result


def saved_records(store,kind,tenant,project,error):
    with closing(store.connect()) as db:
        rows=db.execute("SELECT id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                        (kind,tenant,project,MAX_RECORDS+1)).fetchall()
    if len(rows)>MAX_RECORDS:
        error("PROJECT_ARTIFACT_LIMIT","This bounded workspace exceeds its artifact allowance",503)
    try:
        records=[verify_record(json.loads(row[1]),error,kind=kind,identifier=row[0],project=project) for row in rows]
        return sorted(records,key=lambda row:row["created_at"],reverse=True)
    except (ValueError,TypeError,KeyError):
        error("ARTIFACT_INTEGRITY_ERROR","Immutable project records could not be verified",503)


def reserve_record(db,kind,identity,project,error):
    count=db.execute("SELECT COUNT(*) FROM objects WHERE kind=? AND tenant_id=? AND project_id=?",
                     (kind,identity.tenant_id,project)).fetchone()[0]
    if count>=MAX_RECORDS:
        error("PROJECT_ARTIFACT_LIMIT","At most 100 immutable artifacts of each kind fit this bounded preparation workspace",409)


def preview_readiness():
    from science import MODEL_VERSION, SCIENCE_BUILD_HASH
    records=[]
    failed=[]
    for filename in ("numerical-qualification.json","aij-case-a-diagnostic.json"):
        path=ROOT/"validation"/filename
        if not path.is_file():
            records.append({"record":filename,"status":"required_evidence_unavailable"})
            continue
        try:
            raw=path.read_bytes()
            data=json.loads(raw)
        except (OSError,ValueError):
            records.append({"record":filename,"status":"required_evidence_unreadable"})
            continue
        record={"record":filename,"sha256":"sha256:"+hashlib.sha256(raw).hexdigest(),"independent_signoff":False}
        if filename=="numerical-qualification.json":
            record["release_gates"]=data.get("release_gates",{})
            failed.extend({"id":name,"state":"failed","source_record":filename} for name,passed in record["release_gates"].items() if passed is False)
            record["claim_scope"]=data.get("claim_scope")
        else:
            record.update(status=data.get("status"),admission_failures=data.get("admission_failures",[]),metrics=data.get("metrics",{}))
            if record["admission_failures"]:
                failed.append({"id":"urban_method_model_form_admission","state":"failed","source_record":filename,
                               "reasons":record["admission_failures"]})
        records.append(record)
    return {"method_version":MODEL_VERSION,"science_build_hash":SCIENCE_BUILD_HASH,"qualified":False,
            "status":"research_preview_unqualified","failed_gates":failed,"records":records,
            "limitations":["Phase A software and conditional numerical previews do not establish urban scientific validity.",
                           "Uploading evidence or compiling a detailed case cannot erase retained failed gates."]}


def resource_observations(store,tenant,project):
    observations=[]
    for run in store.list_runs(tenant,project):
        seconds=run.get("resource_use",{}).get("worker_seconds")
        if isinstance(seconds,(int,float)) and not isinstance(seconds,bool) and math.isfinite(seconds) and seconds>=0:
            observations.append({"run_id":run["id"],"status":run["status"],"model_version":run["manifest"]["model_version"],
                                 "worker_seconds":seconds,"basis":"recorded_worker_monotonic_execution_time"})
    values=[row["worker_seconds"] for row in observations]
    return {"observations":observations,"count":len(values),"median_worker_seconds":median(values) if values else None,
            "detailed_cost_calibrated":False,"currency_quote":None,
            "limits":["These are recorded Phase A research-worker observations, not OpenFOAM execution timings.",
                      "No calibrated detailed runtime, cloud price or cash quote follows from them."]}


def checked_native_records(store,tenant,project,error):
    """Read every published attempt, including historical retry receipts.

    Only the worker's scoped immutable objects and actual artifact readback have
    execution authority. An evidence payload claiming native execution does not.
    """
    from api.detailed_execution import (JOB_KIND,RECEIPT_KIND,MAX_JOBS,MAX_ATTEMPTS,
                                       _check_job,_logical,_verify_native_output)
    with closing(store.connect()) as db:
        jobs=db.execute("SELECT id,project_id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                        (JOB_KIND,tenant,project,MAX_JOBS+1)).fetchall()
        receipts=db.execute("SELECT id,project_id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                            (RECEIPT_KIND,tenant,project,MAX_JOBS*MAX_ATTEMPTS+1)).fetchall()
    if len(jobs)>MAX_JOBS or len(receipts)>MAX_JOBS*MAX_ATTEMPTS:
        error("PROJECT_ARTIFACT_LIMIT","Detailed review exceeds this bounded workspace",503)
    result=[]
    try:
        indexed={row["id"]:_check_job(json.loads(row["data"]),identifier=row["id"],tenant=tenant,project=project) for row in jobs}
        readback_bytes=0
        case_bundles={}
        for row in receipts:
            record=json.loads(row["data"])
            if (record.get("schema_version")!="phase-b-execution-receipt-v1" or record.get("id")!=row["id"]
                    or record.get("project_id")!=project or record.get("qualified") is not False
                    or record.get("content_hash")!=digest(_logical(record))
                    or not re.fullmatch(r"dreceipt_[0-9a-f]{32}",record["id"])):
                raise ValueError("Native receipt row identity changed")
            current=indexed.get(record["execution_id"])
            if (current is None or current["case_id"]!=record["case_id"] or current["frozen"]!=record["frozen"]
                    or isinstance(record.get("fence"),bool) or not isinstance(record.get("fence"),int)
                    or not 1<=record["fence"]<=current["_fence"]
                    or isinstance(record.get("attempt"),bool) or not isinstance(record.get("attempt"),int)
                    or not 1<=record["attempt"]<=current["attempt"]):
                raise ValueError("Native attempt is not bound to its committed job")
            case=store.get(CASE_KIND,record["case_id"],tenant)
            if case is None:
                raise ValueError("Native attempt's immutable case is missing")
            verify_record(case,error,kind=CASE_KIND,identifier=record["case_id"],project=project)
            if (case["content_hash"]!=record["frozen"]["case_content_hash"]
                    or case["bundle_checksum"]!=record["frozen"]["case_bundle_checksum"]
                    or case["scenario_hash"]!=record["frozen"]["scenario_hash"] or case["rights"]!=record["rights"]
                    or case["manifest"]["method"]!=record["frozen"]["compiler_method"]
                    or record["frozen"]["rights"]!=record["rights"]
                    or digest(record["frozen"]["runtime_identity"])!=record["frozen"]["runtime_digest"]):
                raise ValueError("Native attempt case/rights identity changed")
            if case["id"] not in case_bundles:
                bundle=store.read_artifact(case["_artifact"],case["bundle_checksum"])
                files,size=checked_case_files(bundle["files"])
                if (bundle["case_id"]!=case["id"] or bundle["project_id"]!=project
                        or bundle["manifest"]!=case["manifest"] or bundle["scenario_hash"]!=case["scenario_hash"]
                        or digest(bundle["scenario"])!=case["manifest"]["scenario_hash"]
                        or digest(bundle["config"])!=case["manifest"]["config_hash"]
                        or files!=case["manifest"]["api_binding"]["file_checksums"]
                        or len(files)!=case["file_count"] or size!=case["file_bytes"]):
                    raise ValueError("Native frozen compiler bytes changed")
                case_bundles[case["id"]]=bundle
                readback_bytes+=len(canonical(bundle).encode("utf-8"))
                if bundle["scenario"].get("source_allocation")!=case["manifest"]["api_binding"].get("source_allocation"):
                    raise ValueError("Native inventory allocation binding changed")
                allocation_dependencies(store,bundle["scenario"],tenant,error,config=bundle["config"])
            payload=store.read_artifact(record["_artifact"],record["artifact_checksum"])
            readback_bytes+=len(canonical(payload).encode("utf-8"))
            if readback_bytes>128*1024*1024:
                error("PROJECT_ARTIFACT_LIMIT","Native review exceeds its 128 MiB verified readback allowance",503)
            original={key:value for key,value in record.items() if key not in {"_artifact","artifact_checksum","content_hash"}}
            native=payload["native_execution"]
            if payload["receipt"]!=original or digest(native)!=record["native_execution_hash"]:
                raise ValueError("Immutable native receipt bytes changed")
            attempt=deepcopy(current)
            attempt.update(_fence=record["fence"],attempt=record["attempt"])
            _verify_native_output(native,attempt)
            attestation=native["attestation"]
            if (attestation.get("runtime_identity")!=record["frozen"]["runtime_identity"]
                    or attestation.get("manifest_hash")!=digest(case["manifest"])
                    or attestation.get("scenario_hash")!=case["manifest"]["scenario_hash"]
                    or attestation.get("config_hash")!=case["manifest"]["config_hash"]
                    or attestation.get("prepared_file_hashes")!=case["manifest"]["file_hashes"]
                    or attestation.get("shell") is not False or attestation.get("uploaded_commands") is not False):
                raise ValueError("Native runtime/compiler/input provenance changed")
            diagnostics=deepcopy(native["diagnostics"])
            if native.get("analysis") is not None:
                diagnostics["field_analysis"]={key:deepcopy(value) for key,value in native["analysis"].items() if key!="probe_series"}
            if record["diagnostics"]!=diagnostics or record["resources"]!=native.get("resources",{}):
                raise ValueError("Native diagnostic/resource projection differs from immutable output")
            expected="failed" if native["status"]=="failed" else "cancelled" if native["status"]=="cancelled" else "complete_unqualified" if native["diagnostics"]["numerical_verification_passed"] else "diagnostic_only"
            if record["status"]!=expected or record["numerical_verification_passed"]!=native["diagnostics"]["numerical_verification_passed"]:
                raise ValueError("Native execution state changed")
            result.append({"record":record,"native":native,"job":current})
        referenced={record["record"]["id"] for record in result}
        if any(job["receipt_id"] is not None and job["receipt_id"] not in referenced for job in indexed.values()):
            raise ValueError("Committed native attempt receipt is missing")
        return sorted(result,key=lambda item:(item["record"]["created_at"],item["record"]["id"]))
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        error("ARTIFACT_INTEGRITY_ERROR","Published native execution/attempt bytes or scope could not be verified",503)


def native_summary(item):
    record,native=item["record"],item["native"]
    analysis=native.get("analysis")
    if isinstance(analysis,dict):
        # Ordinary readiness contains no field arrays, receptor values or raw
        # histories. The checked receipt/ZIP remains the exact diagnostic view.
        analysis={key:deepcopy(value) for key,value in analysis.items()
                  if key in {"schema_version","qualified","numerical_verification_passed","limited_checks_passed","gates",
                             "terminal_iteration","temporal_support","C_unit","reported_C_unit","ug_per_kg","unexecuted_required_gates"}}
    mesh=native.get("mesh")
    mesh_summary=None if not isinstance(mesh,dict) else {key:deepcopy(mesh.get(key)) for key in
        ("actual_execution_verified","qualified","cell_count","connected_regions","check_mesh","native_report_hash")}
    return {"receipt_id":record["id"],"execution_id":record["execution_id"],"case_id":record["case_id"],
        "created_at":record["created_at"],"attempt":record["attempt"],"fence":record["fence"],"status":record["status"],
        "qualified":False,"numerical_verification_passed":record["numerical_verification_passed"],
        "content_hash":record["content_hash"],"native_execution_hash":record["native_execution_hash"],
        "artifact_checksum":record["artifact_checksum"],"artifact_readback_verified":True,
        "rights":deepcopy(record["rights"]),"runtime_digest":record["frozen"]["runtime_digest"],
        "compiler_method":deepcopy(record["frozen"]["compiler_method"]),"resources":deepcopy(record["resources"]),
        "mesh":mesh_summary,"analysis":analysis,"issues":deepcopy(native["diagnostics"].get("issues",[])),
        "receipt_url":f"/api/projects/{record['project_id']}/detailed-executions/{record['execution_id']}/receipt" if item["job"]["receipt_id"]==record["id"] else None,
        "download_url":f"/api/projects/{record['project_id']}/detailed-executions/{record['execution_id']}/download" if item["job"]["receipt_id"]==record["id"] else None,
        "current_attempt":item["job"]["receipt_id"]==record["id"],
        "evidence_basis":"byte_verified_native_diagnostic_not_preregistered_scientific_gate",
        "results_eligible_for_comparison":False}


def current_native_runtime():
    from api.detailed_execution import native_runtime_readiness,_runtime_identity
    try:
        runtime=native_runtime_readiness()
        _runtime_identity(runtime)
        if runtime.get("qualified") is not False:
            raise ValueError("Runtime setup cannot qualify a scientific method")
        return runtime
    except (ValueError,TypeError,OSError):
        return {"available":False,"qualified":False,"state":"runtime_unavailable_fail_closed"}


def checked_case_files(files):
    if not isinstance(files,dict) or not 1<=len(files)<=MAX_CASE_FILES:
        raise ValueError("Compiler file count exceeds bounded case support")
    total=0
    result={}
    for name,text in files.items():
        if not isinstance(name,str) or not isinstance(text,str) or not name or len(name)>240 or "\\" in name or ":" in name or "\0" in text:
            raise ValueError("Case file names/content must be controlled text")
        path=PurePosixPath(name)
        if path.is_absolute() or any(part in {".",".."} for part in name.split("/")) or "//" in name:
            raise ValueError("Case files may not escape their case directory")
        if path.parts[0] not in {"0","constant","system"} and name not in {"README.md","README.txt","manifest.json","compiler-manifest.json"}:
            raise ValueError("Compiler emitted an unsupported case file")
        if not re.fullmatch(r"[A-Za-z0-9_. /-]+",name) or name.endswith("/") or any(part != part.strip() for part in path.parts):
            raise ValueError("Compiler emitted an invalid case path")
        if name.casefold() in {"urban-air-case-manifest.json","urban-air-case-inputs.json"} or name.casefold() in {item.casefold() for item in result}:
            raise ValueError("Compiler emitted a duplicate or reserved case path")
        content=text.encode("utf-8")
        total+=len(content)
        if total>MAX_CASE_BYTES:
            raise ValueError("Generated case exceeds the 32 MB text bundle allowance")
        result[name]={"sha256":"sha256:"+hashlib.sha256(content).hexdigest(),"bytes":len(content)}
    return result,total


def checked_compiler_manifest(manifest,scenario,config,file_checksums,method_status):
    """Reject unbound claims even if the generated file names look safe."""
    if (not isinstance(manifest,dict) or manifest.get("schema_version")!="phase-b-case-v1"
            or manifest.get("qualified") is not False or manifest.get("runtime_available") is not False
            or manifest.get("solver_executed") is not False
            or manifest.get("scenario_hash")!=digest(scenario) or manifest.get("config_hash")!=digest(config)
            or manifest.get("file_hashes")!={name:row["sha256"] for name,row in file_checksums.items()}):
        raise ValueError("Compiler did not freeze exact case dependencies or retained execution limits")
    method=manifest.get("method")
    if (not isinstance(method,dict) or not method.get("id") or not method.get("compiler_version")
            or method.get("distribution")!="OpenFOAM Foundation" or str(method.get("version"))!="13"
            or not re.fullmatch(r"[0-9a-f]{40}",method.get("source_commit",""))
            or any(not re.fullmatch(r"sha256:[0-9a-f]{64}",method.get(key,""))
                   for key in ("compiler_build_hash","source_audit_hash"))):
        raise ValueError("Compiler method/distribution identity is absent or malformed")
    authority=method_status()
    for key in ("id","distribution","version","source_commit","compiler_version","compiler_build_hash","source_audit_hash"):
        if method.get(key)!=authority.get(key):
            raise ValueError("Compiler manifest differs from the captured method identity")
    if len(canonical(manifest).encode("utf-8"))>MAX_RECORD_BYTES-10_000:
        raise ValueError("Compiler manifest exceeds immutable record allowance")


def make_router(*,identity_dependency,store_dependency,required,project_resource,error,physical_hash,authorizer):
    from science.phase_b import CaseConfig,build_case,method_status
    from science.errors import ScienceError
    from evidence.allocation import AllocationGeometry,AllocationControls,allocate_inventory,ALLOCATION_BUILD_HASH
    from api.responses import Scenario

    class DetailedCaseRequest(Input):
        scenario_id: Identifier
        expected_version: Annotated[int,Field(strict=True,ge=1)]
        title: Name="Detailed method preparation"
        config: CaseConfig

    class SourceAllocationRequest(Input):
        scenario_id: Identifier
        expected_version: Annotated[int,Field(strict=True,ge=1)]
        expected_scenario_hash: Annotated[str,Field(pattern=r"^sha256:[0-9a-f]{64}$")]
        inventory_evidence_id: Identifier
        expected_inventory_hash: Annotated[str,Field(pattern=r"^sha256:[0-9a-f]{64}$")]
        title: Name="Conservative traffic source allocation"
        replace_manual_sources: Annotated[bool,Field(strict=True)]
        geometry: AllocationGeometry
        controls: AllocationControls

        @model_validator(mode="after")
        def explicit_replacement(self):
            if self.replace_manual_sources is not True:
                raise ValueError("Explicit replacement of manual sources in a NEW child scenario is required")
            return self

    class SourceAllocationResult(Output):
        allocation: SourceAllocationRecord
        scenario: Scenario

    router=APIRouter(tags=["Phase B preparation"])

    def scoped_record(store,kind,identifier,project,identity,db=None):
        required(store,"project",project,identity,db)
        def lookup(connection):
            return connection.execute("SELECT project_id,data FROM objects WHERE kind=? AND id=? AND tenant_id=?",
                           (kind,identifier,identity.tenant_id)).fetchone()
        if db is None:
            with closing(store.connect()) as connection:
                row=lookup(connection)
        else:
            row=lookup(db)
        if row is None or row[0] != project:
            error("NOT_FOUND","Resource not found in this project/tenant",404)
        try:
            record=json.loads(row[1])
        except (ValueError,TypeError):
            error("ARTIFACT_INTEGRITY_ERROR","Committed artifact record is unreadable",503)
        return verify_record(record,error,kind=kind,identifier=identifier,project=project)

    def permit(check,identity,project,record,action,rights=None):
        # Tenant comes only from the already scoped Store lookup/verified
        # principal, never from arbitrary extra fields in a scientific payload.
        check.require(identity,project,action=action,identifier=record["id"],tenant_id=identity.tenant_id,
                      resource_project_id=record["project_id"],rights=record["effective_rights"] if rights is None else rights)

    def frozen_dependencies(store,record,project,identity,db=None):
        pending=[record]
        resolved={}
        while pending:
            parent=pending.pop()
            for binding in parent.get("dependencies",[]):
                child=scoped_record(store,EVIDENCE_KIND,binding["id"],project,identity,db)
                if (child["content_hash"]!=binding["content_hash"] or child["kind"]!=binding["kind"]
                        or child["effective_rights"]!=binding["rights"]):
                    error("ARTIFACT_INTEGRITY_ERROR","Frozen evidence dependency identity or permissions changed",503)
                if child["id"] not in resolved:
                    resolved[child["id"]]=child
                    if len(resolved)>10:
                        error("ARTIFACT_INTEGRITY_ERROR","Frozen evidence dependency graph exceeds bounded support",503)
                    pending.append(child)
        return list(resolved.values())

    def processing_call(function,*args):
        try:
            return function(*args)
        except ValidationError:
            error("EVIDENCE_VALIDATION_FAILED","Evidence semantics or frozen campaign binding are invalid",422)
        except ValueError as exc:
            reason=" ".join(str(exc).split())[:300]
            error("EVIDENCE_VALIDATION_FAILED",reason or "Evidence semantics or frozen campaign binding are invalid",422)

    def allocation_permit(check,identity,project,action):
        def enforce(record):
            rights=record.get("effective_rights",record.get("source_rights",record.get("rights")))
            permit(check,identity,project,record,action,rights)
        return enforce

    def allocations(store,project,identity):
        with closing(store.connect()) as db:
            rows=db.execute("SELECT id FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                            (ALLOCATION_KIND,identity.tenant_id,project,MAX_RECORDS+1)).fetchall()
        if len(rows)>MAX_RECORDS:
            error("PROJECT_ARTIFACT_LIMIT","Allocation workspace exceeds its bounded support",503)
        return [scoped_allocation(store,row[0],identity.tenant_id,project,error) for row in rows]

    @router.get("/api/projects/{project_id}/source-allocations/context")
    def allocation_context(project_id:str,scenario_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        scenario=project_resource(required(store,"scenario",scenario_id,identity),project_id)
        try:
            Scenario.model_validate(scenario)
            valid=scenario.get("id")==scenario_id and physical_hash(scenario)==scenario.get("content_hash")
        except (ValueError,TypeError,KeyError):
            valid=False
        if not valid:
            error("ARTIFACT_INTEGRITY_ERROR","Scenario identity/input hash changed",503)
        return {"schema_version":"phase-b-source-allocation-context-v1","project_id":project_id,
            "scenario_id":scenario_id,"scenario_version":scenario["version"],"scenario_hash":scenario["content_hash"],
            "coordinate_frame_hash":digest(scenario["coordinate_frame"]),"coordinate_frame":scenario["coordinate_frame"],
            "manual_source_count":len(scenario["sources"]),"admitted_species":["passive_tracer"],
            "admitted_background":["independent_inflow"],"allocation_build_hash":ALLOCATION_BUILD_HASH,
            "geometry_kind":"local_metre_polyline","units":"m","qualified":False}

    @router.post("/api/projects/{project_id}/source-allocations",status_code=201,response_model=SourceAllocationResult)
    def allocation_create(project_id:str,body:SourceAllocationRequest,response:Response,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        from api.main import scenario_version
        from science.geometry import validate_scenario
        required(store,"project",project_id,identity)
        parent=project_resource(required(store,"scenario",body.scenario_id,identity),project_id)
        try:
            Scenario.model_validate(parent)
            valid=parent.get("id")==body.scenario_id and physical_hash(parent)==parent.get("content_hash")
        except (ValueError,TypeError,KeyError):
            valid=False
        if not valid:
            error("ARTIFACT_INTEGRITY_ERROR","Allocation parent snapshot changed",503)
        if parent["version"]!=body.expected_version or parent["content_hash"]!=body.expected_scenario_hash:
            error("VERSION_CONFLICT","Expected immutable scenario version/hash differs",409)
        inventory=scoped_record(store,EVIDENCE_KIND,body.inventory_evidence_id,project_id,identity)
        if inventory["kind"]!="inventory":
            error("INVENTORY_KIND_REQUIRED","Select an immutable source inventory",422)
        if inventory["content_hash"]!=body.expected_inventory_hash:
            error("VERSION_CONFLICT","Expected immutable inventory hash differs",409)
        check=authorizer.check()
        enforce=allocation_permit(check,identity,project_id,"derive")
        for record in (parent,inventory):
            enforce(record)
        allocation_dependencies(store,parent,identity.tenant_id,error,require=enforce,allow_rederive=True)
        geometry,controls=body.geometry.model_dump(),body.controls.model_dump()
        try:
            plan=allocate_inventory(deepcopy(inventory["payload"]),geometry,deepcopy(parent),controls)
            core=deepcopy(parent)
            core.pop("source_allocation",None)
            rights={key:parent["source_rights"][key] and inventory["effective_rights"][key] for key in ("derive","solve","export")}
            core.update(sources=deepcopy(plan["sources"]),source_rights=rights,
                        source_label=f"Conservative passive-tracer road allocation from {inventory['id']}; supplied inputs remain unqualified")
            core["diagnostics"]=validate_scenario(core)
            identifier=new_id("allocation")
            child=scenario_version(core,project_id,body.title,parent,[{"type":"inventory_sources_replace",
                "allocation_id":identifier,"inventory_evidence_id":inventory["id"],"inventory_content_hash":inventory["content_hash"]}])
        except (ValueError,ValidationError) as exc:
            error("SOURCE_ALLOCATION_INVALID"," ".join(str(exc).split())[:300],422)
        except ScienceError as exc:
            error(exc.code,exc.message,422,exc.issues)
        payload={"schema_version":"phase-b-source-allocation-artifact-v1","parent_scenario":deepcopy(parent),
                 "inventory_payload":deepcopy(inventory["payload"]),"geometry":geometry,"controls":controls,"plan":plan}
        try:
            pointer,checksum=store.atomic_artifact(identifier,1,payload)
            if checksum!=digest(payload) or store.read_artifact(pointer,checksum)!=payload:
                raise ValueError("Source allocation readback changed")
        except (OSError,ValueError):
            error("ALLOCATION_STORAGE_FAILED","Source allocation bytes were not durably verified; no child was published",503)
        precedence={"observed":0,"provider-modelled":1,"simulated":2,"illustrative":3}
        summary=allocation_summary(plan)
        record={"schema_version":"phase-b-source-allocation-record-v1","id":identifier,"project_id":project_id,"title":body.title,
            "created_at":utc_now(),"parent_scenario_id":parent["id"],"parent_scenario_hash":parent["content_hash"],
            "parent_snapshot_hash":digest(parent),"scenario_id":child["id"],"scenario_hash":child["content_hash"],
            "inventory_evidence_id":inventory["id"],"inventory_content_hash":inventory["content_hash"],
            "rights":rights,"effective_rights":rights,"effective_evidence_kind":max((parent["evidence_kind"],inventory["effective_evidence_kind"]),key=precedence.get),
            "allocation_build_hash":plan["allocation_build_hash"],"plan_hash":plan["content_hash"],"summary":summary,
            "status":"allocated_unexecuted","qualified":False,"_artifact":pointer,"artifact_checksum":checksum}
        record["content_hash"]=digest(logical_record(record))
        child["source_allocation"]={"schema_version":"phase-b-source-allocation-binding-v1","allocation_id":identifier,
                                    "allocation_content_hash":record["content_hash"]}
        with store.transaction() as db:
            required(store,"project",project_id,identity,db)
            reserve_record(db,ALLOCATION_KIND,identity,project_id,error)
            current_parent=project_resource(required(store,"scenario",parent["id"],identity,db),project_id)
            current_inventory=scoped_record(store,EVIDENCE_KIND,inventory["id"],project_id,identity,db)
            enforce(current_parent)
            enforce(current_inventory)
            allocation_dependencies(store,current_parent,identity.tenant_id,error,db=db,require=enforce,allow_rederive=True)
            if current_parent!=parent or current_inventory!=inventory:
                error("ARTIFACT_INTEGRITY_ERROR","Inventory/source authority changed before allocation publication",503)
            Store.put(db,ALLOCATION_KIND,identity.tenant_id,record,project_id)
            Store.put(db,"scenario",identity.tenant_id,child,project_id)
        response.headers.update(check.headers())
        return {"allocation":public_allocation(record),"scenario":child}

    @router.get("/api/projects/{project_id}/source-allocations",response_model=list[SourceAllocationRecord])
    def allocation_list(project_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        return [public_allocation(record) for record in allocations(store,project_id,identity)]

    @router.get("/api/projects/{project_id}/source-allocations/{allocation_id}",response_model=SourceAllocationRecord)
    def allocation_get(project_id:str,allocation_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        return public_allocation(scoped_allocation(store,allocation_id,identity.tenant_id,project_id,error))

    @router.get("/api/projects/{project_id}/source-allocations/{allocation_id}/download",response_class=JSONResponse)
    def allocation_download(project_id:str,allocation_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        record=scoped_allocation(store,allocation_id,identity.tenant_id,project_id,error)
        scenario=project_resource(required(store,"scenario",record["scenario_id"],identity),project_id)
        check=authorizer.check()
        enforce=allocation_permit(check,identity,project_id,"export")
        enforce(scenario)
        allocation_dependencies(store,scenario,identity.tenant_id,error,require=enforce)
        try:
            payload=store.read_artifact(record["_artifact"],record["artifact_checksum"])
        except (OSError,ValueError):
            error("ARTIFACT_INTEGRITY_ERROR","Allocation bytes changed before export",503)
        return JSONResponse({"allocation":public_allocation(record),"inputs":payload},headers={"Cache-Control":"no-store",
            "Content-Disposition":f'attachment; filename="{allocation_id}.json"',**check.headers()})

    @router.get("/api/projects/{project_id}/qualification",response_model=ReadinessRecord)
    def qualification(project_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        method=method_status()
        if method.get("qualified") is not False:
            error("METHOD_STATUS_INVALID","Detailed qualification has not been established",503)
        evidence=saved_records(store,EVIDENCE_KIND,identity.tenant_id,project_id,error)
        cases=saved_records(store,CASE_KIND,identity.tenant_id,project_id,error)
        from science.phase_b.qualification import qualification_requirements
        runtime=current_native_runtime()
        native=checked_native_records(store,identity.tenant_id,project_id,error)
        native_summaries=[native_summary(item) for item in native]
        method.update(compiler_preparation_state=method["state"],runtime_available=runtime.get("available") is True,
            runtime_digest=runtime.get("runtime_digest"),solver_executed=any(item["native"].get("solver_executed") is True
                and item["record"]["frozen"]["compiler_method"]["compiler_build_hash"]==method["compiler_build_hash"] for item in native),
            state="verified_native_runtime_unqualified" if runtime.get("available") is True else "runtime_unavailable_unqualified")
        requirements=qualification_requirements()
        scientific={"status":"unqualified","qualified":False,"scientific_gates_passed":False,
            "preregistered_protocol":None,"trusted_scientific_gate_receipt_count":0,"independent_review_verified":False,
                "reason":"Byte-verified native diagnostics are available separately; no approved preregistered qualification protocol or independent review is established."}
        observations={"B01":sum(item["native"].get("solver_executed") is True for item in native),
            "B02":sum(isinstance(item["native"].get("mesh"),dict) and item["native"]["mesh"].get("actual_execution_verified") is True for item in native),
            "B04":sum(isinstance(item["native"].get("analysis"),dict) for item in native),"B09":len(native),
            "B12":sum(isinstance(item["record"].get("resources"),dict) and item["record"]["resources"].get("wall_seconds") is not None for item in native)}
        summaries=[]
        for record in evidence:
            summary={key:value for key,value in public_record(record).items() if key!="payload"}
            # Lists remain bounded metadata. Detail/download routes retain every
            # raw/QC/scored row rather than multiplying them across 100 records.
            summary["summary"]={key:value for key,value in summary["summary"].items()
                                if key not in {"rows","segments","qc_exclusions","outside_reserved_support"}}
            summaries.append(summary)
        return {"schema_version":"phase-b-readiness-v1","project_id":project_id,"status":"unqualified",
                "detailed_method":method,"authorization_method":authorizer.status(),"research_preview":preview_readiness(),
                "packages":[{"id":identifier,"title":title,"state":"in_progress" if identifier!="B11" else "review_pending","remaining":remaining}
                            for identifier,title,remaining in PACKAGES],
                "evidence":summaries,"cases":[public_record(record) for record in cases],
                "local_resource_observations":resource_observations(store,identity.tenant_id,project_id),
                "native_runtime":runtime,"native_executions":native_summaries,"scientific_requirements":requirements,
                "scientific_qualification":scientific,"review_packet_url":f"/api/projects/{project_id}/qualification/review-packet",
                "native_package_observations":{"basis":"byte_verified_project_native_diagnostics_not_scientific_gate_acceptance",
                    "counts":observations,"qualified":False},
                "source_allocations":[public_allocation(record) for record in allocations(store,project_id,identity)]}

    @router.get("/api/projects/{project_id}/qualification/review-packet",response_class=JSONResponse)
    def review_packet(project_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        project=required(store,"project",project_id,identity)
        if not re.fullmatch(r"project_[0-9a-f]{32}",project_id) or project.get("id")!=project_id:
            error("ARTIFACT_INTEGRITY_ERROR","Project review identity is invalid",503)
        from science.phase_b.qualification import qualification_requirements
        evidence=saved_records(store,EVIDENCE_KIND,identity.tenant_id,project_id,error)
        cases=saved_records(store,CASE_KIND,identity.tenant_id,project_id,error)
        native=checked_native_records(store,identity.tenant_id,project_id,error)
        check=authorizer.check()
        export_allocation=allocation_permit(check,identity,project_id,"export")
        allocation_packets=[]
        for record in allocations(store,project_id,identity):
            scenario=project_resource(required(store,"scenario",record["scenario_id"],identity),project_id)
            export_allocation(scenario)
            allocation_dependencies(store,scenario,identity.tenant_id,error,require=export_allocation)
            try:
                payload=store.read_artifact(record["_artifact"],record["artifact_checksum"])
            except (OSError,ValueError):
                error("ARTIFACT_INTEGRITY_ERROR","Review allocation bytes could not be verified",503)
            allocation_packets.append({"allocation_record":public_allocation(record),"plan":payload["plan"],
                "parent_snapshot_hash":record["parent_snapshot_hash"],"artifact_readback_verified":True,
                "execution_authority":"checked_conservative_arithmetic_not_native_assignment_or_species_qualification"})
        inventories=[]
        for record in evidence:
            permit(check,identity,project_id,record,"export")
            for dependency in frozen_dependencies(store,record,project_id,identity):
                permit(check,identity,project_id,dependency,"export")
            item={key:deepcopy(value) for key,value in public_record(record).items() if key not in {"payload","summary"}}
            item.update(payload_hash=digest(record["payload"]),summary_hash=digest(record["summary"]),
                payload_metadata={key:deepcopy(value) for key,value in record["payload"].items() if key not in {"observations","predictions"}},
                summary={key:deepcopy(value) for key,value in record["summary"].items()
                         if key not in {"rows","qc_exclusions","outside_reserved_support","segments"}},
                evidence_authority="declared_import_not_native_solver_or_independent_qualification")
            inventories.append(item)
        frozen_cases=[]
        for case in cases:
            permit(check,identity,project_id,case,"export",case["rights"])
            scenario=project_resource(required(store,"scenario",case["scenario_id"],identity),project_id)
            permit(check,identity,project_id,scenario,"export",scenario["source_rights"])
            allocation_dependencies(store,scenario,identity.tenant_id,error,require=export_allocation)
            try:
                bundle=store.read_artifact(case["_artifact"],case["bundle_checksum"])
                checksums,size=checked_case_files(bundle["files"])
                if (bundle["case_id"]!=case["id"] or bundle["project_id"]!=project_id
                        or bundle["scenario_hash"]!=case["scenario_hash"] or bundle["manifest"]!=case["manifest"]
                        or digest(bundle["scenario"])!=digest(scenario) or physical_hash(scenario)!=case["scenario_hash"]
                        or scenario["source_rights"]!=case["rights"] or digest(bundle["config"])!=case["manifest"]["api_binding"]["config_hash"]
                        or checksums!=case["manifest"]["api_binding"]["file_checksums"]
                        or len(checksums)!=case["file_count"] or size!=case["file_bytes"]):
                    raise ValueError("Frozen review dependencies changed")
            except (OSError,ValueError,TypeError,KeyError):
                error("ARTIFACT_INTEGRITY_ERROR","Review case input/configuration bytes could not be verified",503)
            frozen_cases.append({"case_record":public_record(case),"scenario":deepcopy(scenario),"config":deepcopy(bundle["config"]),
                "artifact_checksum":case["bundle_checksum"],"artifact_readback_verified":True,
                "file_checksums":checksums,"execution_authority":"prepared_inputs_only"})
        attempts=[]
        for item in native:
            record=item["record"]
            permit(check,identity,project_id,record,"export",record["rights"])
            summary=native_summary(item)
            summary.update(frozen=deepcopy(record["frozen"]),diagnostics=deepcopy(record["diagnostics"]),
                runtime_identity=deepcopy(item["native"]["attestation"].get("runtime_identity",record["frozen"]["runtime_identity"])),
                file_provenance=deepcopy(item["native"]["attestation"].get("output_file_hashes",{})),
                application_provenance=deepcopy(item["native"].get("commands",[])),
                limitations=deepcopy(item["native"].get("limitations",[])))
            attempts.append(summary)
        # Preserve cancelled attempts and failures before native publication as
        # lifecycle evidence too; absence of a receipt is never solver evidence.
        from api.detailed_execution import JOB_KIND,MAX_JOBS,_check_job,_public_job
        with closing(store.connect()) as db:
            jobs=db.execute("SELECT id,data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id LIMIT ?",
                            (JOB_KIND,identity.tenant_id,project_id,MAX_JOBS+1)).fetchall()
        if len(jobs)>MAX_JOBS:
            error("PROJECT_ARTIFACT_LIMIT","Detailed review exceeds this bounded workspace",503)
        frozen_by_id={row["case_record"]["id"]:row["case_record"] for row in frozen_cases}
        receipt_ids={item["record"]["id"] for item in native}
        lifecycle=[]
        try:
            for row in jobs:
                job=_check_job(json.loads(row["data"]),identifier=row["id"],tenant=identity.tenant_id,project=project_id)
                frozen_case=frozen_by_id[job["case_id"]]
                if (job["frozen"]["case_content_hash"]!=frozen_case["content_hash"]
                        or job["frozen"]["case_bundle_checksum"]!=frozen_case["bundle_checksum"]
                        or job["frozen"]["rights"]!=frozen_case["rights"]
                        or job["receipt_id"] is not None and job["receipt_id"] not in receipt_ids):
                    raise ValueError("Lifecycle dependency changed during review")
                lifecycle.append(_public_job(job))
        except (ValueError,TypeError,KeyError):
            error("ARTIFACT_INTEGRITY_ERROR","Review lifecycle scope/dependencies could not be verified",503)
        packet={"schema_version":"phase-b-project-review-manifest-v1","project_id":project_id,"project_name":project["name"],
            "generated_at":utc_now(),"qualified":False,"scientific_gates_passed":False,"independent_review_verified":False,
            "status":"evidence_review_required_unqualified","preregistered_protocol":None,"approved_envelope":None,
            "scientific_requirements":qualification_requirements(),"retained_preview_failures":preview_readiness(),
            "cases":frozen_cases,"evidence":inventories,"native_attempts":attempts,"execution_lifecycle":lifecycle,
            "source_allocations":allocation_packets,
            "omitted_bulk_data":"Raw observations, imported predictions, native iteration histories, mesh fields and full logs remain in the individually permission-checked immutable JSON/ZIP artifacts referenced here.",
            "rights_policy":"Cedar export was independently required for each case, source scenario, evidence record, frozen ancestor and native attempt; no permission or source class is upgraded.",
            "review_required":["Preregister and approve the method/runtime/input/regime/envelope qualification protocol before qualifying scientific evidence.",
                "Inspect all retained failures, raw/QC observations, native logs and exact input/configuration/artifact hashes.",
                "Execute every required numerical, independent benchmark, sensitivity, held-out replay, uncertainty and paired no-harm gate.",
                "Obtain an independent credentialed human's scoped review and current release authority; this export provides no signature or signoff."],
            "claim_scope":"Project evidence/provenance review manifest, not a scientist-approved qualification or environmental comparison report."}
        if len(canonical(packet).encode("utf-8"))>MAX_CASE_BYTES:
            error("REVIEW_PACKET_LIMIT","Review metadata exceeds its 32 MB bounded export allowance",413)
        packet["packet_hash"]=digest(packet)
        return JSONResponse(packet,headers={"Content-Disposition":f'attachment; filename="{project_id}-scientific-review.json"',
                                          "Cache-Control":"no-store",**check.headers()})

    @router.post("/api/projects/{project_id}/evidence",status_code=201,response_model=EvidenceRecord)
    def evidence_create(project_id:str,body:EvidenceRequest,response:Response,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        check=authorizer.check()
        candidate={"id":new_id("evidence"),"project_id":project_id}
        permit(check,identity,project_id,candidate,"derive",body.rights.model_dump())
        payload=deepcopy(body.payload)
        rights=body.rights.model_dump()
        effective=deepcopy(rights)
        effective_kind=body.evidence_kind
        dependencies=[]
        def bind(parent):
            nonlocal effective,effective_kind
            parent_rights=parent["effective_rights"]
            permit(check,identity,project_id,parent,"derive")
            for ancestor in frozen_dependencies(store,parent,project_id,identity):
                permit(check,identity,project_id,ancestor,"derive")
            effective={key:effective[key] and parent_rights[key] for key in rights}
            parent_kind=parent["effective_evidence_kind"]
            precedence={"observed":0,"provider-modelled":1,"simulated":2,"illustrative":3}
            effective_kind=max((effective_kind,parent_kind),key=precedence.get)
            dependencies.append({"id":parent["id"],"kind":parent["kind"],"content_hash":parent["content_hash"],
                                 "rights":parent_rights,"evidence_kind":parent_kind})
        if body.kind=="replay_protocol":
            campaign=scoped_record(store,EVIDENCE_KIND,payload["campaign_evidence_id"],project_id,identity)
            if campaign["kind"]!="measurement_campaign":
                error("CAMPAIGN_KIND_REQUIRED","Replay must bind a measurement campaign in this project",422)
            bind(campaign)
            processing_call(validate_protocol_campaign,payload,campaign["payload"])
        if body.kind=="replay_assessment":
            protocol=scoped_record(store,EVIDENCE_KIND,payload["protocol_evidence_id"],project_id,identity)
            if protocol["kind"]!="replay_protocol":
                error("PROTOCOL_KIND_REQUIRED","Replay assessment must bind a saved protocol in this project",422)
            campaign=scoped_record(store,EVIDENCE_KIND,protocol["payload"]["campaign_evidence_id"],project_id,identity)
            if campaign["kind"]!="measurement_campaign":
                error("CAMPAIGN_KIND_REQUIRED","Frozen replay protocol must bind a measurement campaign",422)
            frozen=next((row for row in protocol["dependencies"] if row["id"]==campaign["id"]),None)
            if frozen is None or frozen["content_hash"]!=campaign["content_hash"] or frozen["rights"]!=campaign["effective_rights"]:
                error("ARTIFACT_INTEGRITY_ERROR","Replay protocol dependency differs from its frozen campaign",503)
            for parent in (protocol,campaign):
                bind(parent)
            summary=processing_call(assess_replay,payload,protocol["payload"],campaign["payload"])
        else:
            summary=processing_call(process_evidence,body.kind,payload)
        summary["evidence_basis"]={"declared_kind":body.evidence_kind,"effective_kind":effective_kind,
                                   "dependency_classes":[row["evidence_kind"] for row in dependencies]}
        record={"schema_version":"phase-b-evidence-v1","id":candidate["id"],"project_id":project_id,
                "title":body.title,"kind":body.kind,"evidence_kind":body.evidence_kind,"effective_evidence_kind":effective_kind,"source_label":body.source_label,
                "rights":rights,"effective_rights":effective,"payload":payload,"summary":summary,"dependencies":dependencies,"created_at":utc_now()}
        record["content_hash"]=digest(logical_record(record))
        verify_record(record,error,kind=EVIDENCE_KIND,project=project_id)
        with store.transaction() as db:
            required(store,"project",project_id,identity,db)
            reserve_record(db,EVIDENCE_KIND,identity,project_id,error)
            permit(check,identity,project_id,record,"derive")
            for dependency in dependencies:
                current=scoped_record(store,EVIDENCE_KIND,dependency["id"],project_id,identity,db)
                if current["content_hash"]!=dependency["content_hash"]:
                    error("ARTIFACT_INTEGRITY_ERROR","Evidence dependency changed before immutable publication",503)
                permit(check,identity,project_id,current,"derive")
                for ancestor in frozen_dependencies(store,current,project_id,identity,db):
                    permit(check,identity,project_id,ancestor,"derive")
            Store.put(db,EVIDENCE_KIND,identity.tenant_id,record,project_id)
        response.headers.update(check.headers())
        return public_record(record)

    @router.get("/api/projects/{project_id}/evidence/{evidence_id}",response_model=EvidenceRecord)
    def evidence_get(project_id:str,evidence_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        return public_record(scoped_record(store,EVIDENCE_KIND,evidence_id,project_id,identity))

    @router.get("/api/projects/{project_id}/evidence/{evidence_id}/download",response_model=EvidenceRecord)
    def evidence_download(project_id:str,evidence_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        record=scoped_record(store,EVIDENCE_KIND,evidence_id,project_id,identity)
        dependencies=frozen_dependencies(store,record,project_id,identity)
        check=authorizer.check()
        for item in [record,*dependencies]:
            permit(check,identity,project_id,item,"export")
        return JSONResponse(public_record(record),headers={"Content-Disposition":f'attachment; filename="{record["id"]}.json"',"Cache-Control":"no-store",**check.headers()})

    @router.post("/api/projects/{project_id}/detailed-cases",status_code=201,response_model=DetailedCaseRecord)
    def case_create(project_id:str,body:DetailedCaseRequest,response:Response,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        required(store,"project",project_id,identity)
        scenario=project_resource(required(store,"scenario",body.scenario_id,identity),project_id)
        if scenario.get("id")!=body.scenario_id or not isinstance(scenario.get("version"),int) or isinstance(scenario.get("version"),bool):
            error("ARTIFACT_INTEGRITY_ERROR","Selected scenario identity differs from its committed object",503)
        if scenario["version"]!=body.expected_version:
            error("VERSION_CONFLICT","Expected version does not match the selected immutable scenario",409)
        if physical_hash(scenario)!=scenario["content_hash"]:
            error("ARTIFACT_INTEGRITY_ERROR","Scenario snapshot failed its physical input checksum",503)
        try:
            rights=Rights.model_validate(scenario.get("source_rights",{})).model_dump()
        except ValidationError:
            error("ARTIFACT_INTEGRITY_ERROR","Selected scenario has invalid source permission metadata",503)
        check=authorizer.check()
        for action in ("derive","solve"):
            permit(check,identity,project_id,scenario,action,rights)
            allocation_dependencies(store,scenario,identity.tenant_id,error,config=body.config.model_dump(mode="json"),
                                    require=allocation_permit(check,identity,project_id,action),current_method=True)
        with closing(store.connect()) as db:
            reserve_record(db,CASE_KIND,identity,project_id,error)
        try:
            config=body.config.model_dump(mode="json")
            compiled=build_case(deepcopy(scenario),config)
            manifest=deepcopy(compiled["manifest"])
            file_checksums,total=checked_case_files(compiled["files"])
            checked_compiler_manifest(manifest,scenario,config,file_checksums,method_status)
        except ScienceError as exc:
            error(exc.code,exc.message,422,exc.issues)
        except (ValueError,KeyError,TypeError):
            error("CASE_COMPILER_OUTPUT_INVALID","Compiler output failed its bounded case/file contract",503)
        manifest["api_binding"]={"scenario_id":scenario["id"],"scenario_version":scenario["version"],"scenario_hash":scenario["content_hash"],
            "source_inventory_hash":digest(scenario["sources"]),"receptor_set_hash":digest(scenario["receptors"]),
            "coordinate_frame_hash":digest(scenario["coordinate_frame"]),"config_hash":digest(body.config.model_dump(mode="json")),
            "file_checksums":file_checksums,"state":"prepared_unexecuted","qualified":False}
        if scenario.get("source_allocation") is not None:
            manifest["api_binding"]["source_allocation"]=deepcopy(scenario["source_allocation"])
        record={"schema_version":"phase-b-case-v1","id":new_id("dcase"),"project_id":project_id,
                "scenario_id":scenario["id"],"scenario_version":scenario["version"],"scenario_hash":scenario["content_hash"],
                "title":body.title,"created_at":utc_now(),"rights":rights,"manifest":manifest,
                "file_count":len(file_checksums),"file_bytes":total,"status":"prepared_unexecuted","qualified":False}
        bundle={"case_id":record["id"],"project_id":project_id,"scenario_hash":scenario["content_hash"],"manifest":manifest,
                "scenario":deepcopy(scenario),"config":body.config.model_dump(mode="json"),"files":compiled["files"]}
        try:
            pointer,checksum=store.atomic_artifact(record["id"],1,bundle)
            if checksum!=digest(bundle) or store.read_artifact(pointer,checksum)!=bundle:
                raise ValueError("Durable case bytes differ from generated bundle")
        except (OSError,ValueError):
            error("CASE_STORAGE_FAILED","Generated case could not be durably verified; no case was published",503)
        record.update(_artifact=pointer,bundle_checksum=checksum)
        record["content_hash"]=digest(logical_record(record))
        verify_record(record,error,kind=CASE_KIND,project=project_id)
        with store.transaction() as db:
            required(store,"project",project_id,identity,db)
            reserve_record(db,CASE_KIND,identity,project_id,error)
            current=project_resource(required(store,"scenario",body.scenario_id,identity,db),project_id)
            if digest(current)!=digest(scenario):
                error("ARTIFACT_INTEGRITY_ERROR","Scenario dependency changed before immutable case publication",503)
            for action in ("derive","solve"):
                permit(check,identity,project_id,current,action,current["source_rights"])
                allocation_dependencies(store,current,identity.tenant_id,error,config=bundle["config"],db=db,
                                        require=allocation_permit(check,identity,project_id,action),current_method=True)
            Store.put(db,CASE_KIND,identity.tenant_id,record,project_id)
        response.headers.update(check.headers())
        return public_record(record)

    @router.get("/api/projects/{project_id}/detailed-cases/{case_id}/download",response_class=Response,
                responses={200:{"description":"Immutable checked case files and frozen inputs",
                                "content":{"application/zip":{"schema":{"type":"string","format":"binary"}}}}})
    def case_download(project_id:str,case_id:str,identity=Depends(identity_dependency),store=Depends(store_dependency)):
        record=scoped_record(store,CASE_KIND,case_id,project_id,identity)
        check=authorizer.check()
        permit(check,identity,project_id,record,"export",record["rights"])
        try:
            bundle=store.read_artifact(record["_artifact"],record["bundle_checksum"])
            files=bundle["files"]
            checksums,total=checked_case_files(files)
            if (bundle["case_id"]!=case_id or bundle["project_id"]!=project_id or bundle["scenario_hash"]!=record["scenario_hash"]
                    or bundle["manifest"]!=record["manifest"] or checksums!=record["manifest"]["api_binding"]["file_checksums"]
                    or physical_hash(bundle["scenario"])!=record["scenario_hash"]
                    or digest(bundle["config"])!=record["manifest"]["api_binding"]["config_hash"]
                    or len(files)!=record["file_count"] or total!=record["file_bytes"]):
                raise ValueError("Case dependency mismatch")
            permit(check,identity,project_id,bundle["scenario"],"export",bundle["scenario"]["source_rights"])
            if bundle["scenario"].get("source_allocation")!=record["manifest"]["api_binding"].get("source_allocation"):
                raise ValueError("Case allocation authority changed")
            allocation_dependencies(store,bundle["scenario"],identity.tenant_id,error,config=bundle["config"],
                                    require=allocation_permit(check,identity,project_id,"export"))
            archive=BytesIO()
            with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as zip_file:
                def write(name,content):
                    info=zipfile.ZipInfo(f"{case_id}/{name}",date_time=(1980,1,1,0,0,0))
                    info.compress_type=zipfile.ZIP_DEFLATED
                    info.external_attr=0o100644 << 16
                    zip_file.writestr(info,content,compresslevel=3)
                for name,text in sorted(files.items()):
                    write(name,text.encode("utf-8"))
                write("urban-air-case-manifest.json",canonical({"schema_version":"phase-b-case-export-v1","case_record":public_record(record)}))
                write("urban-air-case-inputs.json",canonical({"scenario":bundle["scenario"],"config":bundle["config"]}))
        except (OSError,ValueError,KeyError,TypeError,UnicodeError):
            error("ARTIFACT_INTEGRITY_ERROR","Committed detailed case failed its immutable bundle/file checks",503)
        return Response(archive.getvalue(),media_type="application/zip",headers={"Content-Disposition":f'attachment; filename="{case_id}.zip"',"Cache-Control":"no-store",**check.headers()})

    return router
