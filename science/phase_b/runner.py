"""Non-executing, fixed-command contract for a future reviewed Linux worker.

No process launcher is provided: this host has no verified Foundation 13
runtime. A protocol and a positive input report cannot attest actual execution.
"""
from .config import CaseConfig
from .geometry import number, fail


def runner_protocol(config):
    config = config if isinstance(config, CaseConfig) else CaseConfig.model_validate(config)
    return {"schema_version": "phase-b-runner-protocol-v1", "executable": False,
            "runtime_available": False, "runtime_image_digest": None, "required_platform": "Linux",
            "distribution": "OpenFOAM Foundation", "version": "13",
            "shell": False, "user_commands_allowed": False, "working_directory": "worker_owned_immutable_case_copy",
            "max_wall_seconds": config.max_wall_seconds, "max_log_bytes": 16*1024*1024,
            "max_output_bytes": 256*1024*1024, "max_cells": config.max_cells,
            "preparation_commands": [["blockMesh"], ["snappyHexMesh", "-overwrite"],
                                     ["checkMesh", "-allTopology", "-allGeometry"], ["topoSet"],
                                     ["setAtmBoundaryLayer", "-time", "0"]],
            "solve_command": ["foamRun", "-solver", "incompressibleFluid"],
            "solve_blocked_until": ["verified_pinned_runtime_digest", "actual_mesh_quality_and_single_fluid_connectivity",
                                    "expected_patch_names_types_and_counts", "nonempty_source_zones_and_actual_fluid_volumes",
                                    "native_receptor_cell_support", "actual_initialisation_and_ABL_gate"],
            "publication_blocked_until": ["scalar_positivity_and_mass_balance", "velocity_and_scalar_stationarity",
                                          "mesh_domain_closure_sensitivity", "relevant_independent_benchmarks",
                                          "declared_validation_envelope_and_independent_review"],
            "attestation": "protocol_only_no_commands_executed_no_scientific_result"}


def check_source_assignment(manifest, mesh_report):
    """Check arithmetic of an explicit mesh report; never assert its provenance.

    The caller must bind reports to actual mesh bytes/runtime/logs separately.
    geometric_support_volume_m3 is deliberately never used for normalisation.
    """
    if not isinstance(mesh_report, dict) or set(mesh_report) != {"zones"} or not isinstance(mesh_report["zones"], list):
        fail("MESH_REPORT_INVALID", "Supply explicit actual mesh zone counts, volumes and integrated assigned rates.")
    expected = {s["zone"]: s for s in manifest["sources"]}
    domain_volume = 1.0
    for a, b in zip(manifest["domain"]["min_m"], manifest["domain"]["max_m"]):
        domain_volume *= b-a
    reports, rows = {}, []
    for report in mesh_report["zones"]:
        if not isinstance(report, dict) or set(report) != {"zone", "fluid_cell_count", "fluid_volume_m3", "assigned_rate_kg_s"}:
            fail("MESH_REPORT_INVALID", "Mesh source-zone report fields are incomplete or unsupported.")
        zone = report["zone"]
        if not isinstance(zone, str) or zone not in expected or zone in reports:
            fail("MESH_REPORT_INVALID", "Unknown or duplicate source zone.")
        count = report["fluid_cell_count"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= manifest["config"]["max_cells"]:
            fail("SOURCE_ZONE_EMPTY", "Every source zone must contain bounded actual fluid cells.", zone)
        volume = number(report["fluid_volume_m3"], "actual fluid mesh volume")
        assigned = number(report["assigned_rate_kg_s"], "integrated assigned rate")
        if not 0 < volume <= domain_volume or assigned < 0:
            fail("MESH_REPORT_INVALID", "Actual fluid volume must be positive/bounded by domain and assigned mass nonnegative.", zone)
        rate = expected[zone]["rate_kg_s"]
        error = abs(assigned-rate)/rate if rate > 0 else (0.0 if assigned == 0 else float("inf"))
        if error > 0.001:
            fail("SOURCE_MASS_ASSIGNMENT_FAILED", "Integrated source mass differs by more than the proposed 0.1% gate.", zone)
        rows.append({"zone": zone, "fluid_cell_count": count, "fluid_volume_m3": volume,
                     "rate_kg_s": rate, "volume_specific_rate_kg_m3_s": rate/volume,
                     "assigned_rate_kg_s": assigned, "relative_assignment_error": error})
        reports[zone] = report
    if set(reports) != set(expected):
        fail("MESH_REPORT_INVALID", "Actual mesh report must cover every source zone exactly once.")
    return {"arithmetic_checks_passed": True, "actual_execution_verified": False,
            "qualified": False, "report_provenance": "caller_supplied_unattested", "zones": rows}
