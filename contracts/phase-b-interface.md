# Phase B implementation contract — 9 October 2026

Permanent references: blueprint §§6–10, 13–17 and 18.1; execution guide §6, B01–B12. Original documents and Phase A closeout remain unchanged. Owner explicitly authorised starting B; cash limit remains USD0, Free plan only, no cloud activation.

## Ownership and integration

Science agent owns `science/phase_b/`, matching new tests and `docs/phase-b-method.md`. Implement a pinned-distribution neutral RANS case compiler, metre-aligned watertight building surfaces, checked domain/mesh/ABL configuration and a bounded runner contract. Primary official sources only; do not mix OpenFOAM distributions. No external solver outputs or accuracy can be invented. Keep existing `science/*.py` unchanged so prior build identity/results survive.

Root owns `evidence/` strict inventory, campaign QC, held-out replay protocol and paired-decision utilities, matching tests, backlog, decisions and checkpoint. Interface: `EvidenceRequest` in `evidence.schemas` contains `title`, `kind` (`inventory`, `measurement_campaign`, `replay_protocol`), `evidence_kind` (`observed`, `provider-modelled`, `simulated`, `illustrative`), `source_label`, `rights` (`derive`, `solve`, `export`) and a typed `payload`. `process_evidence(kind, payload)` in `evidence.processing` returns an explicit derived summary without promoting scientific qualification. Raw typed payload is retained alongside it. Review/physical campaign completion cannot be inferred from an upload.

Resumed 10 October: fourth kind `replay_assessment` resolves a frozen same-project protocol and its campaign, inheriting both dependency rights/evidence limits. `assess_replay(payload, protocol_payload, campaign_payload)` verifies submitted interval-mean prediction species/units/clock-corrected time support/exact held-out IDs, lists QC exclusions and per-location/time-block concentration metrics. Only simulated/illustrative assessment labels are permitted. Submitted solver identity is a frozen claim, not attested execution; `model_qualified=false` regardless of arithmetic targets. No Phase A end-state field is reclassified as a measured interval mean. `effective_evidence_kind` retains the most restricted illustrative/simulated dependency basis.

Platform agent owns `api/phase_b.py`, registration in `api/main.py`, generated contracts and new API tests. Use existing immutable Store transactions and scoped identity, no new database tables or migration required. Register an APIRouter from `create_app`; reuse `required`, `project_resource`, `local_identity`, `get_store` through explicit registration arguments or imports that avoid circular import-time execution.

Frontend agent owns new `web/src/components/EvidenceWorkspace.tsx`, necessary navigation/viewState/types/CSS and frontend tests. Add a real project Evidence workspace; retain Phase A controls, research limits, no-WebGL and responsive/keyboard conventions. No fake detailed result, qualification badge or placeholder action.

## HTTP interface

- `GET /api/projects/{project_id}/qualification`: `schema_version: phase-b-readiness-v1`, project_id, `status: unqualified`, `detailed_method` (id, distribution, version, state, runtime_available, qualified=false, limitations[]), `research_preview` (method/version and retained failed gates), `packages` (B01–B12 id/title/state/remaining[]), `evidence` (immutable summaries), `cases` (immutable summaries). Missing runtime does not affect evidence ingestion.
- `POST /api/projects/{project_id}/evidence`: strict EvidenceRequest; reply full immutable record containing id/project_id/title/kind/evidence_kind/source_label/rights/payload/summary/created_at/content_hash. Reject nonfinite/bool-as-number, unknown fields, incompatible units/intervals and invalid rights. Do not persist invalid submissions.
- `GET /api/projects/{project_id}/evidence/{evidence_id}`: tenant/project-scoped immutable full record with checksum verification.
- `GET /api/projects/{project_id}/evidence/{evidence_id}/download`: rights-gated JSON, authenticated, no-store, filename based on generated ID, never user path.
- `POST /api/projects/{project_id}/detailed-cases`: scenario_id, expected_version, config. Reply immutable case id/project_id/scenario_id/scenario_hash/title/created_at/content_hash/manifest; config model imported from science compiler. Freeze source/scenario/config/compiler hashes. Requires derive/solve rights; does not enqueue a fake T2 run.
- `GET /api/projects/{project_id}/detailed-cases/{case_id}/download`: rights-gated ZIP of checked generated files and manifest, authoritative checksum verified, paths/compiler controlled. Scenario updates do not modify previous cases.

Compiler seam: `science.phase_b.build_case(scenario: dict, config: dict) -> {manifest: dict, files: dict[str,str]}` and exported Pydantic `CaseConfig`. `science.phase_b.method_status() -> dict` supplies detailed_method. ZIP generation is server-side and bounded; names may not escape a case directory. No shell=True, user-supplied code or unauthenticated filesystem path. Container/runtime execution and scientific admission remain separate gates.

The frontend should read project-specific qualification and list saved artifacts, offer structured inventory entry and campaign/replay JSON upload with clear semantic instructions, display QC exclusions rather than deleting raw readings, prepare a case from the selected scenario with explicit required parameters and download it. Root will provide exact data schemas/sample fixtures. Loading/errors/missing geometry/restricted exports must be real API states.

## Acceptance for this local increment

Actual project→inventory/campaign/protocol→immutable retrieval/download→scenario case preparation flow; invalid/duplicate/QC/leakage/unit/background/rights tests; tenant/project isolation; no accidental T2 promotion; physical dimensions/watertightness/source and receptor checks; frontend production and keyboard/axe checks; Phase A regression. B remains in progress until actual solver execution, relevant independent benchmarks, field/held-out evidence and independent method review meet the preserved guide's gates. No money/site/participant requests are required to implement these foundations.
