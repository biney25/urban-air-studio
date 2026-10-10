"""Pure bounded case compiler. Generating a case never qualifies a prediction."""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import PurePosixPath

from .config import CaseConfig
from .geometry import fail, prepare_geometry, prepare_domain
from .dictionaries import fields, mesh_files, flow_scalar_files, abl_profile, KAPPA, CMU, C1, C2, SIGMA_EPS
from .identity import (METHOD_ID, COMPILER_VERSION, COMPILER_BUILD_HASH, SOURCE_AUDIT_HASH,
                       SOURCE_HASHES, DEPENDENCIES, COMMIT, digest)
from .runner import runner_protocol

MAX_BUNDLE_BYTES = 32*1024*1024
MAX_FILES = 1000
LIMITATIONS = [
    "Prepared stationary neutral RANS/passive tracer case; no solver, mesh or concentration result has been executed here.",
    "Flat ground and closed extruded buildings only; no terrain, undercrofts, porous structures or inferred roofs.",
    "Single defined neutral wind with constant source rates/background; no calm, stability, transient forecast or time-average/AQI claim.",
    "No chemistry, deposition, particle settling, vegetation, thermal effects, plume rise, indoor transport or health model.",
    "Atmospheric coefficient consistency is a proposal; actual empty-domain profile, rough-wall/y+ and obstacle benchmarks remain unexecuted.",
    "Source boxes are finite geometric supports; actual selected fluid-cell volumes, integrated mass and receptor support must pass before solving.",
    "No independent validation envelope, physical campaign, held-out replay, runtime/cost class or independent scientific review is established."
]


def method_status():
    return {"id": METHOD_ID, "distribution": "OpenFOAM Foundation", "version": "13", "source_commit": COMMIT,
            "compiler_version": COMPILER_VERSION, "compiler_build_hash": COMPILER_BUILD_HASH,
            "source_audit_hash": SOURCE_AUDIT_HASH, "state": "compiler_prepared_runtime_unexecuted",
            "runtime_available": False, "runtime_image_digest": None, "solver_executed": False, "qualified": False,
            "limitations": list(LIMITATIONS)}


def _check_files(files):
    if len(files) > MAX_FILES:
        fail("COMPUTE_LIMIT_EXCEEDED", "Generated file count exceeds the controlled case limit.")
    for path, text in files.items():
        parsed = PurePosixPath(path)
        if (not isinstance(text, str) or not path or len(path) > 200 or not re.fullmatch(r"[A-Za-z0-9_.]+(?:/[A-Za-z0-9_.]+)*", path) or parsed.is_absolute()
                or any(p in (".", "..", "") for p in path.split("/"))
                or (path != "README.md" and parsed.parts[0] not in ("0", "constant", "system"))):
            fail("CASE_PATH_INVALID", "Generated case path/text violates the controlled bundle contract.")
    total = sum(len(text.encode("utf-8")) for text in files.values())
    if total > MAX_BUNDLE_BYTES:
        fail("COMPUTE_LIMIT_EXCEEDED", "Generated text bundle exceeds 32 MiB.")
    return total


def build_case(scenario: dict, config: dict) -> dict:
    """Return immutable-by-value text files and a checksum-bound manifest.

    Does not write files, launch commands, make network requests, use a shell,
    enqueue a run or infer scientific admission from successful preparation.
    """
    config = CaseConfig.model_validate(config.model_dump() if isinstance(config, CaseConfig) else config)
    try:
        # Strict JSON copy, also refuses nonfinite numbers in retained metadata.
        scenario = json.loads(json.dumps(scenario, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        fail("GEOMETRY_INVALID", "Scenario must contain only finite JSON data.")
    transform, solids, sources, receptors, bounds = prepare_geometry(scenario, config)
    domain = prepare_domain(solids, sources, receptors, bounds, config)
    files = mesh_files(solids, sources, domain, config)
    files.update(fields(solids, domain, config))
    files.update(flow_scalar_files(sources, receptors, config))
    files["README.md"] = """# Urban Air Studio — prepared Foundation OpenFOAM 13 case

This original generated case is unqualified. No solver has executed and no
concentration, accuracy, time average, forecast, runtime or cost is established.
The authoritative external case manifest binds the exact pinned source,
compiler/dependencies, input snapshot, resolved defaults and file checksums.

Coordinates are metres in a rotated right-handed wind frame, z above flat
ground. U is m/s; p is kinematic pressure m²/s²; k is m²/s²; epsilon is m²/s³;
nut/nu/D are m²/s. C is kg/m³, converted to µg/m³ by multiplying by 1e9.
Background µg/m³ is multiplied by 1e-9; each source g/s is multiplied by 1e-3.
Finite source boxes are clipped at ground. They represent a uniform volumetric
release, without a stack jet, plume rise, source activity schedule or chemistry.
semiImplicitSource absolute rates are divided by the ACTUAL selected fluid-cell
zone volume. A geometric box volume must never substitute for that mesh volume.

A reviewed Linux worker must pin the Foundation 13 runtime digest and enforce
the manifest's fixed-command protocol, paths, wall-time and output limits.
Mesh quality/connectivity/patches, nonempty source zones and actual volumes,
native receptor cell support and ABL initialisation are mandatory pre-run gates.
setAtmBoundaryLayer sets internal U/k/epsilon profiles after meshing. No shell
script or arbitrary executable is included. The steady iteration index is not a
physical averaging duration. Flow residual stopping is deliberately disabled:
the scalar function object is outside SIMPLE residualControl; velocity AND C
stationarity, positivity and mass balance need external checks at the actual run.

Empty-domain ABL, obstacle/tracer benchmarks, manufactured transport, three
mesh levels, domain/closure sensitivity, held-out observations and independent
method review remain unexecuted. Successful generation is never T2 qualification.
OpenFOAM attribution: OpenFOAM Foundation, https://openfoam.org/; the pinned
official source is GPL. These original generated inputs contain no copied solver
source; using/distributing a runtime remains subject to its own license.
"""
    total = _check_files(files)
    geometry = [{"object_id": s["id"], "patch": s["patch"], "height_m": s["height_m"],
                 "area_m2": s["mesh"]["area_m2"], "volume_m3": s["mesh"]["volume_m3"],
                 "vertices": len(s["mesh"]["vertices"]), "triangles": len(s["mesh"]["faces"]),
                 "watertight": s["mesh"]["watertight"], "manifold_edge_count": s["mesh"]["manifold_edge_count"],
                 "surface_path": f"constant/geometry/{s['patch']}.obj"} for s in solids]
    gates = [{"id": name, "state": "unexecuted", "required": True} for name in (
        "actual_mesh_quality_connectivity_patch_identity", "actual_source_zone_volume_and_mass_assignment",
        "actual_native_receptor_support", "empty_domain_ABL_and_wall_yplus", "independent_obstacle_flow_benchmark",
        "no_source_uniform_manufactured_diffusion_transport", "scalar_positivity_conservation_boundary_fluxes",
        "velocity_scalar_residuals_stationarity", "three_mesh_domain_closure_sensitivity",
        "independent_tracer_benchmark", "held_out_measurements_and_uncertainty", "independent_scientific_review")]
    manifest = {"schema_version": "phase-b-case-v1", "state": "compiler_prepared_runtime_unexecuted",
                "method": method_status(), "qualified": False, "runtime_available": False, "solver_executed": False,
                "scenario_hash": digest(scenario), "config_hash": digest(config.model_dump()),
                "geometry_hash": digest({k: scenario[k] for k in ("origin", "coordinate_frame", "buildings", "sources", "receptors")}),
                "config": config.model_dump(), "compiler_source_hashes": dict(SOURCE_HASHES),
                "compiler_dependencies": dict(DEPENDENCIES), "coordinate_frame": deepcopy(scenario["coordinate_frame"]),
                "coordinate_transform": transform, "domain": domain, "geometry": geometry,
                "sources": sources, "receptors": receptors, "field_dimensions": {
                    "U": [0,1,-1,0,0,0,0], "p": [0,2,-2,0,0,0,0], "k": [0,2,-2,0,0,0,0],
                    "epsilon": [0,2,-3,0,0,0,0], "nut": [0,2,-1,0,0,0,0], "C": [1,-3,0,0,0,0,0]},
                "scalar_contract": {"species": "passive_tracer_mass", "concentration_unit": "kg/m3", "background_kg_m3": config.background_ug_m3*1e-9,
                    "emission_rate_kg_s": sum(s["rate_kg_s"] for s in sources), "input_rate_unit": "g/s", "g_to_kg": 1e-3,
                    "ug_to_kg": 1e-9, "diffusivity": "D_molecular + nut / turbulent_Schmidt_number",
                    "alphal": config.molecular_diffusivity_m2_s/config.kinematic_viscosity_m2_s,
                    "alphat": 1/config.turbulent_schmidt_number, "normalisation": "actual_selected_fluid_cell_zone_volume_only",
                    "wall_flux": "zero_normal_gradient_no_deposition", "averaging_duration_s": None,
                    "temporal_support": "stationary_conditional_mean_proposal_not_forecast_or_observed_average",
                    "advection_scheme": "conservative_first_order_Gauss_upwind_requires_diffusion_sensitivity"},
                "atmospheric_contract": {"stability": "neutral", "displacement_height_m": 0.0, "ground_z_m": 0.0,
                    "kappa": KAPPA, "Cmu": CMU, "C1": C1, "C2": C2, "sigmak": 1.0, "sigmaEps": SIGMA_EPS,
                    "equilibrium_relation": "sigmaEps=kappa^2/((C2-C1)*sqrt(Cmu))", "profile_at_reference": abl_profile(config, config.reference_height_m),
                    "actual_homogeneity_verified": False, "rough_wall_first_cell_yplus_verified": False,
                    "top_boundary": "fixed_profile_U_k_epsilon_at_domain_top", "lateral_boundary": "symmetryPlane",
                    "initialisation": "fixed_setAtmBoundaryLayer_after_mesh_not_yet_executed"},
                "iteration_contract": {"index_is_physical_seconds": False, "limit": config.flow_iterations,
                    "linear_solver_absolute_tolerance": config.residual_tolerance,
                    "scalar_linear_solver_absolute_tolerance": config.scalar_residual_tolerance,
                    "automatic_flow_only_stopping": False,
                    "external_stationarity_gate_required": True},
                "runner_protocol": runner_protocol(config), "gates": gates,
                "proposed_reviewable_targets": {"source_assignment_relative_error": 0.001, "steady_mass_imbalance": 0.01,
                                               "receptor_stationarity_drift": 0.01, "mesh_domain_metric_change": 0.05},
                "bundle_bytes": total, "file_hashes": {p: "sha256:"+hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in sorted(files.items())},
                "limitations": list(LIMITATIONS)}
    return {"manifest": manifest, "files": files}
