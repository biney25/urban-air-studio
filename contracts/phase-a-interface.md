# Phase A integration contract v1

All endpoints are under `/api`. JSON, UTC timestamps, finite numeric values, metres in local ENU coordinates, longitude/latitude for import. A local-only loopback development identity is explicit; production must fail closed without configured JWT verification. References in output/reference and planning outputs remain unchanged.

## Objects

Project: `{id,name,created_at,baseline_scenario_id}`.
Scenario: `{id,project_id,name,version,content_hash,parent_id,created_at,origin:{longitude,latitude},buildings:[{id,name,footprint:[[x,y],...],height_m}],sources:[{id,name,x,y,z,rate_g_s}],receptors:[{id,name,x,y,z,protected}],diagnostics,evidence_kind}`. ID denotes immutable snapshot. Version is an integer, expected_version guards operations. No closing duplicate vertex in local footprints. All geometry synthetic by default and labelled.
Run: `{id,project_id,scenario_id,status,progress,stage,created_at,manifest,result,error,cancel_requested,attempt}`. Status: queued/preparing/solving/verifying/complete_unqualified/failed/cancelled. `manifest` includes frozen conditions, scenario hash, model/schema versions, exact inputs, evidence status/limitations. Terminal failed verification never publishes a completed result.
Conditions: `{wind_speed_m_s,wind_from_deg,diffusivity_m2_s,background_ug_m3,duration_s}`. Species passive_tracer. Same conditions and receptor support required for comparisons.
Result: `{field:{schema_version:'fields-v1',extent:[xmin,ymin,xmax,ymax],nx,ny,height_m,values,mask,units:'ug/m3'},metrics:[{receptor_id,name,value_ug_m3,valid,reason}],diagnostics,evidence_kind:'simulated',qualification:'research_preview',limitations}`. flat values/mask row-major y then x. Invalid values null. Actual simulation, never random environmental numbers.

## Endpoints

- GET /health; GET /capabilities
- GET /fixtures/campus: returns standalone synthetic GeoJSON FeatureCollection with buildings (height_m), point sources (rate_g_s,z), and receptors (z,protected). Origin approx Delhi is fictional; no provider dependency.
- GET /projects; POST /projects `{name}`; GET /projects/{p} -> `{project,scenarios,runs,imports}`
- POST /projects/{p}/imports `{filename,geojson,rights:{derive:true,solve:true,export:true},source_label}` -> `{id,scenario,audit}`. Validated WGS84 GeoJSON Polygon buildings/Point sources/receptors, reject unsupported geometry with structured issues. No fetching arbitrary URLs.
- POST /projects/{p}/baseline `{scenario_id}` -> project; only audited scenario in same project.
- GET /scenarios/{s}; POST /scenarios/{s}/branch `{name}` -> scenario
- POST /scenarios/{s}/operations `{expected_version,operations:[{type:'building_update',object_id,changes:{height_m?,dx?,dy?,rotation_deg?}} | {type:'source_update',object_id,changes:{rate_g_s?,x?,y?,z?}} | {type:'receptor_update',object_id,changes:{x?,y?,z?}}]}` -> new scenario. May expand operations add/delete with explicit validation.
- POST /projects/{p}/quote `{scenario_ids,conditions,tier:'T1'}` -> `{id,cases,maximum_seconds,expires_at,limitations}`; this is local resource allowance, not billed currency.
- POST /projects/{p}/runs `{quote_id,idempotency_key}` -> `{runs:[Run]}`
- GET /runs/{r}; GET /runs/{r}/result -> Result; POST /runs/{r}/cancel; POST /runs/{r}/retry -> new attempt/run
- POST /projects/{p}/compare `{baseline_run_id,alternative_run_id}` -> `{baseline_run_id,alternative_run_id,metrics:[{receptor_id,name,baseline_ug_m3,alternative_ug_m3,delta_ug_m3,percent_change,protected}],qualification,limitations}`. Reject unpaired conditions/receptor geometry/species/model versions and incomplete runs. Scientific input changes retained in manifests. No percentage when baseline zero.
- POST /projects/{p}/reports `{baseline_run_id,alternative_run_id,title}` -> `{id,...}`; GET /reports/{id}; GET /reports/{id}/download?format=html|json. Draft only, immutable exact evidence, limitations, provenance, checksum manifest. HTML print-ready.

Errors: HTTP 4xx `{detail:{code,message,issues?}}` or standard Pydantic validation errors. Object scope enforced server-side. Do not use in-process fire-and-forget computation: separate worker scans/claims durable jobs, uses lease/fencing token and transactionally publishes after atomic files, cancellation wins before commit. Local SQLite/files adapter may exist for runnable Windows development; PostGIS/S3 production adapter not falsely claimed deployed.

## Ownership

Frontend agent owns web/. Backend agent owns api/, contracts/generated schemas, backend tests, backend requirements. Science agent owns science/, datasets/, validation/, science tests and method documentation. Root owns integration tests, infrastructure, scripts, delivery ledger and root documentation. Coordinate schema changes with all owners before mutation.
