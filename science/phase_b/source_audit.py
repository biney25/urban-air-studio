"""Read fixed official Foundation source references; never executes solver code."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import urllib.request

COMMIT = "18870c24d21c6b982e2cdec27b2f59738cca5f90"
BASE = f"https://raw.githubusercontent.com/OpenFOAM/OpenFOAM-13/{COMMIT}/"
PATHS = [
    "src/atmosphericModels/derivedFvPatchFields/atmBoundaryLayer/atmBoundaryLayer.C",
    "src/atmosphericModels/derivedFvPatchFields/atmBoundaryLayer/atmBoundaryLayer.H",
    "src/atmosphericModels/derivedFvPatchFields/nutkAtmRoughWallFunction/nutkAtmRoughWallFunctionFvPatchScalarField.H",
    "src/fvModels/general/semiImplicitSource/semiImplicitSource.H",
    "src/functionObjects/solvers/scalarTransport/scalarTransport.H",
    "src/functionObjects/solvers/scalarTransport/scalarTransport.C",
    "tutorials/incompressibleFluid/pitzDailySteady/constant/physicalProperties",
    "tutorials/incompressibleFluid/pitzDailySteady/constant/momentumTransport",
    "tutorials/incompressibleFluid/pitzDailySteady/system/controlDict",
    "tutorials/incompressibleFluid/pitzDailySteady/system/fvSolution",
    "tutorials/incompressibleFluid/pitzDailyScalarTransport/system/functions",
    "src/fvModels/general/semiImplicitSource/semiImplicitSource.C",
    "src/finiteVolume/fvMesh/fvCellZone/fvCellZone.C",
    "src/meshTools/zoneGenerators/cell/generatedCellZone/generatedCellZone.C",
    "src/MomentumTransportModels/momentumTransportModels/RAS/kEpsilon/kEpsilon.C",
    "src/atmosphericModels/derivedFvPatchFields/nutkAtmRoughWallFunction/nutkAtmRoughWallFunctionFvPatchScalarField.C",
    "src/MomentumTransportModels/momentumTransportModels/derivedFvPatchFields/wallFunctions/epsilonWallFunctions/epsilonWallFunction/epsilonWallFunctionFvPatchScalarField.C",
    "src/MomentumTransportModels/momentumTransportModels/derivedFvPatchFields/wallFunctions/nutWallFunctions/nutWallFunction/nutWallFunctionFvPatchScalarField.C",
    "src/sampling/probes/probes.C",
    "src/sampling/probes/probes.H",
    "applications/utilities/preProcessing/setAtmBoundaryLayer/setAtmBoundaryLayer.C",
    "etc/caseDicts/mesh/generation/meshQualityDict",
    "etc/caseDicts/mesh/generation/snappyHexMeshDict.cfg",
    "etc/caseDicts/functions/solvers/scalarTransport.cfg",
    "tutorials/incompressibleFluid/pitzDailySteady/system/fvSchemes",
    "bin/scalarTransportFoam",
    "applications/solvers/foamRun/foamRun.C",
    "applications/utilities/mesh/generation/blockMesh/blockMesh.C",
    "applications/utilities/mesh/generation/snappyHexMesh/snappyHexMesh.C",
    "applications/utilities/mesh/manipulation/topoSet/topoSet.C",
    "applications/utilities/mesh/manipulation/checkMesh/checkMesh.C",
    "src/topoSetSources/cellSources/boxToCell/boxToCell.C",
    "src/topoSetSources/cellZoneSources/setToCellZone/setToCellZone.C",
    "tutorials/fluid/stackPlume/0/U.orig",
    "tutorials/fluid/stackPlume/0/nut",
    "tutorials/fluid/stackPlume/system/scalarTransport",
    "tutorials/fluid/stackPlume/system/setAtmBoundaryLayerDict",
    "tutorials/incompressibleFluid/windAroundBuildings/system/snappyHexMeshDict",
    "src/topoSetSources/cellSources/boxToCell/boxToCell.H",
    "src/topoSetSources/cellZoneSources/setToCellZone/setToCellZone.H",
]


def main():
    root = Path(__file__).resolve().parents[2]
    target = root / ".runtime" / "phase-b-reference"
    if os.name == "nt":
        target = Path("\\\\?\\" + str(target))
    target.mkdir(parents=True,exist_ok=True)
    def get(path):
        try:
            with urllib.request.urlopen(BASE+path,timeout=40) as response:
                data = response.read()
            destination = target / path
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
            return {"path":path,"url":BASE+path,"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data),"retrieved":True}
        except Exception as exc:
            return {"path":path,"url":BASE+path,"retrieved":False,"error_type":type(exc).__name__}
    with ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(get,PATHS))
    record = {"distribution":"OpenFOAM Foundation","version":"13","source_commit":COMMIT,
              "repository":"https://github.com/OpenFOAM/OpenFOAM-13","files":files,
              "solver_executed":False,"license":"Official source GPL; local raw references are not executable inputs",
              "removed_missing_reference":"tutorials/incompressibleFluid/pitzDailyScalarTransport/constant/fvModels (absent; replaced by implemented semiImplicitSource and cell-zone sources)",
              "audit_scope":"Exact pinned source syntax review; no compiled runtime or numerical compatibility result"}
    (Path(__file__).resolve().parent / "official-source-audit.json").write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"source_commit":COMMIT,"retrieved":sum(f["retrieved"] for f in files),"missing":[f["path"] for f in files if not f["retrieved"]],"solver_executed":False}))


if __name__ == "__main__":
    main()
