# Deployed demo versus local Urban Air Studio

Comparison checked against the current application routers, `infra/public_demo.py`, and the Docker build on 11 October 2026. The hosted edition runs the same frontend, API and T1 science code through a separate resource-limited deployment wrapper. Having an API route present does not mean its external data, runtime or scientific qualification is available.

The reported Create project failure was a shared frontend defect: the form silently disabled submission below eight planning-question characters even though the API accepts one character or an omitted question. The fix makes this question optional and leaves browser validation and API errors visible; it does not substitute an invented planning goal.

| Function | Local installation | Free public deployment |
| --- | --- | --- |
| Create/list/reopen projects; categories and planning brief | Implemented | Same API; private anonymous browser tenant; eight projects per browser |
| Map navigation and place selection | Worldwide coordinates, bounded study area | Same frontend and public basemap; geographic context, not a national pollution model |
| OSM buildings/roads and GFS weather requests; context receipts | External providers plus locally retained snapshots | Same broker, but fresh requests depend on provider availability. Hosted checks encountered OSM unavailable and GFS rate limiting. Local snapshots are not uploaded |
| Reviewed building heights/exclusions; site preparation preview and frozen baseline | Implemented with explicit assumptions and permissions | Same preparation API when the selected context is available; no silent replacement of missing data |
| Supported GeoJSON import and fictional sample | Implemented | Same import/fixture API; request size capped at 1 MB |
| Immutable baselines, alternative branches, supported building/source/receptor edits and programme constraints | Implemented | Same endpoints and checks; source permission/integrity rules retained |
| T0 geometry diagnostics | Implemented | Available through the ordinary scenario diagnostics |
| T1 quotes, actual worker jobs, progress, results, cancellation and retry | Real local research-preview solver | Real single background worker, bounded quotas below; results remain unvalidated |
| Matching-condition comparisons; fields, metrics and design changes | Implemented | Same API/frontend. Real no-change, source-rate change, building-height change and incompatible-wind checks passed on hosted HTTPS |
| Draft report creation/list/reopen and HTML/JSON download | Implemented with hashes and caveats | Same API; verified hosted downloads. Retain downloaded copies before reset |
| Research/source-compliance export | Cedar-checked complete research packages | Same permission and licence checks; never an unrestricted raw-data export |
| Emissions inventories, meteorology/measurement evidence, QC and evidence downloads | Implemented bounded preparation tools | Same evidence API and native Cedar engine; actual export Allow/Deny verified. Inputs and scientific gates still determine what is admitted |
| Conservative road-source allocation and retained allocation records/download | Implemented | Same preparation endpoints, subject to permissions, dependencies and public request/storage limits |
| Native detailed case compilation/download | OpenFOAM Foundation 13 case compiler | New compilation requests explicitly disabled. Download route remains, but public sessions start without compiled cases |
| Detailed CFD execution, retry/cancel, native diagnostics/receipts/artifact downloads | Uses the installed WSL/OpenFOAM worker; diagnostic, scientifically unqualified | No OpenFOAM/WSL runtime or detailed worker. Execution mutations return `DEMO_RUNTIME_NOT_HOSTED`; readiness reports unavailable. Historical private native cases/receipts are not copied |
| Strands assistant, scoped model/tool calls, proposals and retained conversations | Local Ollama/Qwen model and Strands runtime | No model server hosted; assistant messages disabled and status unavailable. Private conversations and model weights excluded |
| Finite source-control response studies: preview/create/list/retrieve/compare | Implemented bounded enumeration, not qualified optimisation | Same endpoints; requested cases must fit the T1 caps. Studies requesting more than two cases in one quote are rejected explicitly |
| Frozen report review links, expiry/revocation and anchored comments | Implemented possession-scoped review | Same endpoints within current temporary store. A link cannot survive a reset and is not verified reviewer identity |
| Authorization and isolation | Local development identity; prepared Cognito mode; real Cedar permission checks | Signed Secure/HttpOnly browser session, tenant isolation, same-origin writes and real Cedar checks; no personal login or verified organisation membership |
| Storage, previous projects and evidence | Persistent local SQLite/artifacts; prepared PostgreSQL/PostGIS/S3 adapters | Fresh temporary SQLite/files. No private projects/uploads/provider caches, runtime databases, model weights or raw benchmark evidence migrated. No persistent database/bucket configured |
| Operator/settings and production services | Production controls require configured deployment; some operator views available locally | Operator views hidden. No hosted Cognito, AWS managed services, queue service, autoscaling or accepted production monitoring/recovery system |

## Exact public resource limits

- Per browser: eight projects, twelve cumulative T1 cases until reset, two active cases, 250 stored objects, 120 requests/minute and twenty writes/minute. The run limit counts completed/failed cases as well as active cases; retry is a new case unless recovering an existing idempotent submission.
- Per quote: two scenarios, 60,000 grid cells maximum per scenario and 120 simulated seconds. Geometry is not silently coarsened to fit.
- Shared service: one T1 worker, four active cases, 100 cumulative cases until reset, 4,000 objects, 1,000 sessions, 600 requests/minute and 128 MB temporary storage. Individual artifact maximum 4 MB.
- Each request body is at most 1 MB. Browser sessions expire after 24 hours; a process restart also invalidates them. The free instance sleeps with inactivity and can take about a minute to start. Files are ephemeral: sleep/restart/redeployment can erase studies/access. Download reports before leaving.

## Gaps shared by both editions

Neither edition is the complete original production blueprint: validated PM/AQI predictions, maintained nationwide scientific datasets, independent physical qualification, full intervention/import coverage, qualified optimisation/surrogates, enterprise identity/review approval and complete production operations remain unfinished. These are product/science gaps, separate from the features omitted by this free deployment. See `blueprint-remaining-20261011.md` for the preserved original-scope gap list.

## Verification boundaries

The existing hosted verification proves actual import/baseline/edit/T1/compare/HTML+JSON report and Cedar Allow/Deny, plus successful map rendering. Routes that were preserved by the wrapper are identified above from source inspection; this does not claim every preserved preparation/review endpoint received a new hosted end-to-end test. Native CFD and AI are explicitly absent, and external-provider availability remains a genuine limitation.
