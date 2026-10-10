# Urban Air Studio

An environmental design workbench for importing a site, preserving a baseline,
editing alternatives and inspecting traceable **research-preview** results.
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

## Architecture and AWS usage

React/TypeScript + Vite/Cesium frontend → FastAPI → scoped immutable Store and
separate preview/native workers. SQLite is the local store; PostGIS/S3/Cognito
adapters are prepared, with relevant tests, but **no AWS cloud deployment**.
Native solver development uses existing Ubuntu22.04 WSL2 and OpenFOAM Foundation13.

**Cedar is the functioning AWS-origin open-source component.** The server checks
derive/solve/export permissions on authoritative scoped records and their frozen
dependencies. Matching permitted requests receive Allow; a restricted export
receives HTTP403 with a real Deny proof. Missing/tampered authorization state
fails closed. Local `cedarpy4.12.0` is a community Python binding to the Cedar
engine; it is not an official AWS SDK, hosted Amazon Verified Permissions, an AI
model or a scientific validator. [AWS introduction](https://aws.amazon.com/about-aws/whats-new/2023/05/cedar-open-source-language-access-control/),
[Cedar](https://github.com/cedar-policy/cedar), [binding](https://github.com/k9securityio/cedar-py).

## Local setup

Python3.13 x64 and Node22.12+ are required. In a fresh checkout:

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

Show real Cedar API decisions without touching live projects:

```powershell
.\.venv\Scripts\python.exe demo/cedar_demo.py
```

This uses a real native engine and real application routes through an isolated
in-process ASGI transport, not a deployed endpoint or mocked authorization.

## Verification and honest limits

The preserved full development edition passed **691 Python/PostGIS tests,
192 frontend tests**, a production build and18 normal/CSP responsive browser
audits. Actual local multi-site import/edit/run/compare/report and native
allocation/worker/protected-download flows were checked. These historical results
are not a claim that this reduced public review package has rerun that full suite.

All numerical results remain **diagnostic/research previews**. Scalar mesh
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

Framework/dependency credits include Cedar/cedarpy, OpenFOAM Foundation/CFD Direct,
React, Vite, Cesium, FastAPI, NumPy/SciPy, Shapely/PROJ and other locked dependencies.
See `THIRD_PARTY_NOTICES.md` and retained licence files. Original native utilities
linked to OpenFOAM are GPL-3.0-or-later. External benchmark files, installed solver
binaries, private databases, copied conversation, account screenshots and original
product reference documents are excluded from this review package and retained
locally. No blanket new application licence is granted by this draft.

## Build provenance

Local checkpoints record development on October9–10,2026. There was no Git
repository/history in the working folder at compliance review. Any first real
commit is a snapshot at its actual creation time; earlier commits will not be
fabricated or backdated. Checkpoints supplement provenance but do not recreate
missing Git history. This audited source package was published with the owner's
approval at [biney25/urban-air-studio](https://github.com/biney25/urban-air-studio).
Private development data and later unaudited changes remain local.
