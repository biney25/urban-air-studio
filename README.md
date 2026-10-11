# Urban Air Studio

A map-first environmental workbench: choose a place, review building inputs,
preserve a baseline, edit an alternative and compare traceable **research-preview**
results in a readable report. Upload is an additional route for permitted plans.
Built for the Environmental Hacks Air track by **PhoenixFires**.

## Problem and working product

Site planners need to explore how geometry and emissions affect a proposed design
without losing the source assumptions, permissions or provenance behind a result.
Urban Air Studio supports independent projects rather than a fixed Delhi example.
It currently provides GeoJSON import/audit, immutable building/source/receptor
edits, local research runs, compatible comparisons and draft HTML/JSON reports.
Its detailed workspace adds inventories, measurement QC, reserved replay
assessments, conservative road-source allocation into a new child scenario,
pinned OpenFOAM case preparation and a cancellable native diagnostic worker.
Protected downloads and a scientific review manifest retain input/runtime hashes
and unresolved qualification gates. Supplied data is explicitly fictional.

The local edition now provides geographic Explore, explicit bounded OSM geometry
and Open-Meteo GFS weather-context inspection, retained snapshots and reviewed
map-to-baseline preparation. Heights, exclusions and flat-ground assumptions need
explicit review; artificial study sources/assessment points are clearly labelled.
Map navigation supports independent Delhi/India sites and other supported places.
Coverage of a basemap or provider does not establish measured inputs or citywide
scientific accuracy. The shared UI uses bundled licensed Poppins and Inter fonts.

Versioned planning requirements check declared capacity, gaps, access corridors
and protected assessment locations. Real matched runs preserve those requirements.
HTML/JSON reports reopen through a library and source-containing exports are
assembled by the server with applicable complete ODbL notices/derivative/alterations.

The response-study workspace compares **up to three user-selected source-rate
controls**. It previews declared factors, requires explicit confirmation to save
immutable alternatives, uses the normal bounded quote/run path and compares exact
completed pairs. This is finite enumeration, not a qualified optimiser or an AI
prediction of a reduction. Protected-location increases remain visible.

An owner can create a private link to one frozen report, with a bounded lifetime,
anchored append-only comments and checked HTML download. Revocation denies new
checked requests. Review credentials cannot edit projects, run computations,
invoke AI or export other assets. The local link grants access to its holder;
reviewer labels do not verify a person, and local OS/loopback owner trust remains.
This is scoped local review, not enterprise organisation roles or public hosting.
Already downloaded copies cannot be recalled.

## Architecture and AWS usage

React/TypeScript + Vite/Cesium frontend → FastAPI → scoped immutable Store and
separate preview/native workers. SQLite is the local store; PostGIS/S3/Cognito
adapters are prepared, with relevant tests, but **no AWS cloud deployment**.
Native solver development uses existing Ubuntu22.04 WSL2 and OpenFOAM Foundation13.

**Cedar is the functioning AWS-origin open-source component.** The server checks
derive/solve/export permissions on authoritative scoped records and their frozen
dependencies. Matching permitted requests receive Allow; a restricted export
receives HTTP403 with a real Deny proof. Missing/tampered authorization state
fails closed. Local `cedarpy 4.12.0` is a community Python binding to the Cedar
engine; it is not an official AWS SDK, hosted Amazon Verified Permissions, an AI
model or a scientific validator. [AWS introduction](https://aws.amazon.com/about-aws/whats-new/2023/05/cedar-open-source-language-access-control/),
[Cedar](https://github.com/cedar-policy/cedar), [binding](https://github.com/k9securityio/cedar-py).

**Strands Agents is a second functioning AWS-origin open-source component.**
The actual local Ollama model chooses scoped study/evidence/proposal tools.
It cannot select a shell, arbitrary URL, identity, permission flag or run budget.
A proposed edit needs explicit review and the normal API's constraints/rights checks.
Actual inference produced a reviewed semantic change, executed paired T1 trials
and read five evidence-linked metrics. Twelve actual tasks were evaluated: eight
output-guard accepted and four visibly blocked; the full forty-task gate remains
open. Numeric values come from authoritative frozen cards, not unchecked prose.
This is local Strands, not Bedrock or a deployed AWS service. No money was spent.

## Local setup

Python 3.13 x64 and Node 22.12+ are required. In a fresh checkout:

```powershell
python -m venv .venv
python -m pip --python .venv install --no-deps --only-binary=:all: --require-hashes -r api/cedar-requirements.lock
python -m pip --python .venv install -r api/requirements.lock
npm --prefix web ci
$env:UA_MODE = 'local'
$env:AWS_EC2_METADATA_DISABLED = 'true'
.\.venv\Scripts\python.exe scripts/dev.py
```

Open `http://127.0.0.1:5173`. Local preview/evidence/Cedar functions do not need
AWS credentials. Detailed native runs additionally need the exact verified
Foundation13 runtime and an operator receipt; a fresh checkout does not include
that installation. Runtime readiness fails closed until configured. Do not run
installation scripts or reset an existing working environment blindly.

The optional local assistant needs the separately installed portable Ollama runtime,
model weights and isolated Strands environment. Existing configured machines can
reuse `scripts/start_local_assistant.py`; a new machine can deliberately use
`scripts/setup_local_assistant.py`, obtain the declared `qwen3:1.7b` model and run
`scripts/evaluate_local_assistant.py` for an actual acceptance receipt. Ordinary
planning remains available without it. Cold startup may be slow. No model/cloud
credentials or installed binaries are included in this source repository.

Show real Cedar API decisions without touching live projects:

```powershell
.\.venv\Scripts\python.exe demo/cedar_demo.py
```

This uses a real native engine and real application routes through an isolated
in-process ASGI transport, not a deployed endpoint or mocked authorization.

Run the released standalone workflow tests and frontend checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_response_study.py tests/test_report_review.py tests/test_planning.py tests/test_research_export.py tests/test_site_preparation.py tests/test_explore.py
npm --prefix web test
npm --prefix web run build
```

Tests use explicitly synthetic inputs and isolated local stores. The reduced
package excludes private original documents, scientific benchmark data and native
runtime installations; it does not contain every test in the full private edition.

## Verification and honest limits

See [the release verification summary](verification/deadline-summary.json) for
the exact source-bound engineering scope. The latest private source edition passed
**1,004 backend tests** and **274 frontend tests** plus its production build.
The actual retained response-study/report-review flow passed **18 responsive
accessibility audits**, including mint, read, anchored comment, checked download
and revoke. The final core navigation/layout continuation must also pass before
the packager accepts that edition. These counts attest the full private edition;
they are not a claim that all 1,004 tests are included or rerun in this reduced
public package. Separate candidate checks establish clean-package behaviour.
Historical checks apply only to their original editions. Actual local inference and
blocked responses are retained privately; source releases omit user messages,
private model receipts, databases and raw geographic responses.

All numerical results remain **diagnostic/research previews**. The original A–E
programme, full role/approval matrix, professional partner/operating gates and
forty-task AI evaluation are incomplete. Scalar mesh
convergence failed its fixed5% target; common-point checks did not rescue it.
Physical/ABL/closure/held-out/uncertainty/independent-review gates remain open.
No calibrated Delhi/India pollution prediction, PM/AQI forecast, health effect,
regulatory suitability or city-scale/cloud-operation accuracy is established.
Imported measurement/prediction declarations do not attest scientific validity.

## Team, AI and third-party credits

PhoenixFires roster: Shaurya Goyal (event leader), Shrivats Goel, Tushar Aggarwal
([biney25](https://github.com/biney25)), Muskan Pandey. Only biney25 is currently
confirmed as a GitHub account; event usernames are not treated as GitHub handles.
Each person's actual deliverables still need their confirmation in the submission.

OpenAI Codex/ChatGPT coding assistance and focused frontend/platform/science
subagents were used for implementation, research and testing. Antigravity was
prepared as a handoff but **not used to build the product**, per the owner.

Framework/dependency credits include Cedar/cedarpy, Strands, Ollama/Qwen3,
OpenFOAM Foundation/CFD Direct, MapLibre, Poppins/Inter, React, Vite, Cesium,
FastAPI, NumPy/SciPy, Shapely/PROJ and other locked dependencies.
See `THIRD_PARTY_NOTICES.md` and retained licence files. Original native utilities
linked to OpenFOAM are GPL-3.0-or-later. External benchmark files, installed solver
binaries, private databases, copied conversation, account screenshots and original
product reference documents are excluded from this review package and retained
locally. No blanket new application licence is granted by this draft.

## Build provenance

Local checkpoints record development on October 9–11, 2026. A prior audited source
snapshot was published with the owner's approval at
[biney25/urban-air-studio](https://github.com/biney25/urban-air-studio). There was no
working-folder Git history at the first compliance review; subsequent private
commits and public increments retain their actual dates. Checkpoints supplement
provenance without recreating missing history. This new candidate is unpublished
until its exact audit and owner approval are complete. Private development data
and original product reference documents remain local.

## Free public demonstration

Render Free hosting runs the frontend, API, real Cedar and bounded T1 worker with isolated anonymous browser sessions. Its data is temporary and resets on sleep/restart. Native OpenFOAM and local AI remain in the local edition. See [deployment and limitations](docs/free-public-deployment.md).
