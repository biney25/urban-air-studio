"""Fail-closed scientific admission against an immutable, preregistered protocol.

This is an evidence ledger, not a scientist, signer, runner or upload endpoint.
The host must supply trusted receipt/artifact verifiers. JSON claiming a native
execution or a human approval has no authority on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, model_validator


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("A finite number, not a boolean or coercible string, is required")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("A finite number is required")
    return float(value)


Number = Annotated[float, BeforeValidator(_number)]
Hash = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


def content_hash(value) -> str:
    """Hash canonical JSON; callers retain original artifacts separately."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO8601 string with explicit timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Explicit timestamp timezone required")
    return result.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class InputIdentity(StrictModel):
    scenario_hash: Hash
    config_hash: Hash


class Envelope(StrictModel):
    """Every advertised applicability axis is explicit and hashed."""
    processes: Annotated[list[Identifier], Field(min_length=1, max_length=30)]
    geometry_families: Annotated[list[Identifier], Field(min_length=1, max_length=30)]
    regimes: Annotated[list[Identifier], Field(min_length=1, max_length=30)]
    species: Annotated[list[Identifier], Field(min_length=1, max_length=30)]
    site_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    wind_speed_range_m_s: Annotated[list[Number], Field(min_length=2, max_length=2)]
    receptor_height_range_m: Annotated[list[Number], Field(min_length=2, max_length=2)]
    exclusions: Annotated[list[Text], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def bounded(self):
        for name in ("processes", "geometry_families", "regimes", "species", "site_ids", "exclusions"):
            values = getattr(self, name)
            if len(values) != len(set(values)) or any(v.lower() in {"all", "any", "everywhere", "pan-india"} for v in values):
                raise ValueError("Envelope lists must be unique and explicitly bounded")
        for values in (self.wind_speed_range_m_s, self.receptor_height_range_m):
            if values[0] < 0 or values[1] < values[0]:
                raise ValueError("Envelope intervals must be nonnegative and ordered")
        return self


REQUIRED_GATES = {
    "runtime_identity": ("B01", {"native_execution"}),
    "mesh_quality": ("B02", {"native_execution"}),
    "source_assignment": ("B02", {"native_execution"}),
    "receptor_support": ("B02", {"native_execution"}),
    "abl_homogeneity": ("B03", {"native_execution"}),
    "obstacle_wind_validation": ("B03", {"independent_observation"}),
    "array_wind_validation": ("B03", {"independent_observation"}),
    "scalar_no_source": ("B04", {"native_execution"}),
    "scalar_uniform": ("B04", {"native_execution"}),
    "scalar_manufactured": ("B04", {"native_execution"}),
    "scalar_positivity": ("B04", {"native_execution"}),
    "scalar_mass_balance": ("B04", {"native_execution"}),
    "flow_scalar_stationarity": ("B04", {"native_execution"}),
    "inventory_background": ("B05", {"measurement_qc"}),
    "campaign_qc": ("B06", {"measurement_qc"}),
    "mesh_sensitivity": ("B07", {"native_execution"}),
    "domain_sensitivity": ("B07", {"native_execution"}),
    "closure_sensitivity": ("B07", {"native_execution"}),
    "tracer_validation": ("B08", {"independent_observation"}),
    "heldout_replay": ("B08", {"heldout_observation"}),
    "uncertainty_budget": ("B08", {"uncertainty_analysis"}),
    "paired_no_change": ("B10", {"native_paired_execution"}),
    "paired_source_control": ("B10", {"native_paired_execution"}),
    "protected_receptor_no_harm": ("B10", {"native_paired_execution"}),
    "resource_calibration": ("B12", {"runtime_measurement"}),
}
EvidenceBasis = Literal["native_execution", "independent_observation", "heldout_observation", "measurement_qc",
                        "uncertainty_analysis", "native_paired_execution", "runtime_measurement",
                        "illustrative", "prepared_inputs", "imported_prediction"]

# Preserve the guide's proposed starting targets. Additional ABL, manufactured,
# uncertainty and decision targets must be preregistered by the protocol owner.
BASELINE_TARGETS = {
    "source_assignment": ("relative_assignment_error", "fraction", "le", .001),
    "scalar_mass_balance": ("relative_mass_imbalance", "fraction", "lt", .01),
    "flow_scalar_stationarity": ("relative_stationarity_drift", "fraction", "lt", .01),
    "mesh_sensitivity": ("relative_metric_change", "fraction", "lt", .05),
    "domain_sensitivity": ("relative_metric_change", "fraction", "lt", .05),
    "obstacle_wind_validation": ("normalized_vector_rmse", "fraction", "le", .30),
    "array_wind_validation": ("normalized_vector_rmse", "fraction", "le", .30),
    "tracer_validation": ("FAC2", "fraction", "ge", .60),
    "heldout_replay": ("FAC2", "fraction", "ge", .60),
}


def qualification_requirements() -> dict:
    """Read-only catalogue; successful setup/preparation never fills these gates."""
    return {"schema_version": "phase-b-qualification-requirements-v1", "qualified": False,
            "status": "requires_preregistered_protocol_and_executed_evidence",
            "threshold_basis": "preserved_project_proposed_targets_not_industry_certification",
            "gates": [{"gate_id": gate_id, "package": package, "state": "unexecuted",
                       "accepted_evidence_basis": sorted(bases),
                       "proposed_baseline_target": dict(zip(("name", "unit", "operator", "threshold"),
                                                               BASELINE_TARGETS[gate_id])) if gate_id in BASELINE_TARGETS else None,
                       "minimum_distinct_mesh_levels": 3 if gate_id == "mesh_sensitivity" else 1}
                      for gate_id, (package, bases) in REQUIRED_GATES.items()],
            "independent_human_review_required": True,
            "promotion_requirements": ["trusted_execution_or_observation_issuer", "actual_artifact_readback",
                                       "method_runtime_compiler_input_protocol_envelope_identity",
                                       "every_preregistered_case_and_regime", "all_failed_attempts_retained",
                                       "approved_scoped_independent_scientific_review"]}


class MetricTarget(StrictModel):
    name: Identifier
    scope: Identifier
    unit: Identifier
    operator: Literal["lt", "le", "ge", "gt", "eq"]
    threshold: Number


class GateRequirement(StrictModel):
    gate_id: Literal[tuple(REQUIRED_GATES)]
    inputs: Annotated[list[InputIdentity], Field(min_length=1, max_length=100)]
    targets: Annotated[list[MetricTarget], Field(min_length=1, max_length=1000)]
    minimum_artifacts: Annotated[StrictInt, Field(ge=1, le=100)] = 1
    minimum_runs: Annotated[StrictInt, Field(ge=1, le=100)] = 1
    minimum_mesh_levels: Annotated[StrictInt, Field(ge=1, le=10)] = 1

    @model_validator(mode="after")
    def complete(self):
        if len({content_hash(v) for v in self.inputs}) != len(self.inputs):
            raise ValueError("Duplicate frozen gate inputs")
        if len({(v.scope, v.name) for v in self.targets}) != len(self.targets):
            raise ValueError("Duplicate gate metric/scope targets")
        if self.gate_id == "mesh_sensitivity" and (self.minimum_mesh_levels < 3 or len(self.inputs) < 3):
            raise ValueError("Mesh sensitivity requires at least three distinct frozen input levels")
        if self.gate_id in BASELINE_TARGETS:
            name, unit, operator, threshold = BASELINE_TARGETS[self.gate_id]
            matches = [target for target in self.targets if target.name == name]
            if not matches:
                raise ValueError("Missing proposed baseline metric target")
            for target in matches:
                stricter = target.threshold <= threshold if operator in {"lt", "le"} else target.threshold >= threshold
                if target.unit != unit or target.operator != operator or not stricter or target.threshold < 0:
                    raise ValueError("Proposed baseline gate cannot silently be loosened or change units")
                if name == "FAC2" and target.threshold > 1:
                    raise ValueError("FAC2 is a proportion and its target cannot exceed one")
        return self


class QualificationProtocol(StrictModel):
    schema_version: Literal["phase-b-qualification-protocol-v1"]
    protocol_id: Identifier
    registered_at: str
    method_id: Identifier
    compiler_build_hash: Hash
    runtime_build_hash: Hash
    envelope: Envelope
    requirements: Annotated[list[GateRequirement], Field(min_length=len(REQUIRED_GATES), max_length=len(REQUIRED_GATES))]
    limitations: Annotated[list[Text], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def complete(self):
        _time(self.registered_at)
        if {row.gate_id for row in self.requirements} != set(REQUIRED_GATES):
            raise ValueError("All Phase B gates must be declared exactly once")
        return self


class ArtifactReference(StrictModel):
    id: Identifier  # Opaque store ID; never a filesystem or URL supplied by users.
    sha256: Hash
    bytes: Annotated[StrictInt, Field(gt=0, le=1024 * 1024 * 1024)]


class Metric(StrictModel):
    name: Identifier
    scope: Identifier
    unit: Identifier
    value: Number

    @model_validator(mode="after")
    def physical_range(self):
        nonnegative = {"relative_assignment_error", "relative_mass_imbalance", "relative_stationarity_drift",
                       "relative_metric_change", "normalized_vector_rmse", "FAC2"}
        if self.name in nonnegative and self.value < 0:
            raise ValueError("An error magnitude or FAC2 proportion cannot be negative")
        if self.name == "FAC2" and self.value > 1:
            raise ValueError("FAC2 is a proportion in the interval zero to one")
        if self.unit == "count" and (self.value < 0 or not self.value.is_integer()):
            raise ValueError("Count metrics must be nonnegative integers")
        return self


class GateReceipt(StrictModel):
    schema_version: Literal["phase-b-qualification-receipt-v1"]
    receipt_id: Identifier
    gate_id: Literal[tuple(REQUIRED_GATES)]
    attempt_id: Identifier
    recorded_at: str
    method_id: Identifier
    runtime_build_hash: Hash
    compiler_build_hash: Hash
    scenario_hash: Hash
    config_hash: Hash
    protocol_hash: Hash
    envelope_hash: Hash
    status: Literal["completed", "failed", "blocked", "cancelled"]
    evidence_basis: EvidenceBasis
    metrics: Annotated[list[Metric], Field(max_length=1000)]
    artifacts: Annotated[list[ArtifactReference], Field(max_length=100)]
    mesh_level_id: Identifier | None = None
    supersedes: Annotated[list[Identifier], Field(max_length=100)] = []
    limitations: Annotated[list[Text], Field(max_length=100)] = []

    @model_validator(mode="after")
    def complete(self):
        _time(self.recorded_at)
        if len({(v.scope, v.name) for v in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate receipt metrics")
        if len({v.id for v in self.artifacts}) != len(self.artifacts):
            raise ValueError("Duplicate artifact IDs")
        if len(self.supersedes) != len(set(self.supersedes)) or self.receipt_id in self.supersedes:
            raise ValueError("Duplicate or self-superseding receipt")
        return self


def _compare(value, target):
    return {"lt": lambda: value < target.threshold, "le": lambda: value <= target.threshold,
            "ge": lambda: value >= target.threshold, "gt": lambda: value > target.threshold,
            "eq": lambda: value == target.threshold}[target.operator]()


def evaluate_qualification(protocol, records, *, verify_receipt: Callable | None = None,
                           read_artifact: Callable | None = None,
                           review_reference: str | None = None,
                           verify_review: Callable | None = None) -> dict:
    """Build a complete review packet and admit only independently approved scope.

    Trusted host callbacks must return literal True (not truthy dicts/strings).
    verify_receipt receives canonical validated receipt and its content hash;
    read_artifact receives only its opaque artifact ID and returns actual bytes.
    verify_review receives a trusted registry reference and exact packet hash;
    it must validate signer independence, scope, credentials, failure dispositions,
    source rights, protocol approval, revocation/expiry and release authority.
    No implementation of human approval is provided by this module.
    """
    # Revalidate typed objects too: mutable nested lists, model_construct and
    # objects retained by another caller are not an immutable trust boundary.
    protocol = QualificationProtocol.model_validate(protocol)
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError("Supply a bounded ordered ledger of immutable gate receipts")
    receipts = [GateReceipt.model_validate(row) for row in records]
    if len({row.receipt_id for row in receipts}) != len(receipts):
        raise ValueError("Duplicate receipt IDs in qualification ledger")
    if any(_time(a.recorded_at) > _time(b.recorded_at) for a, b in zip(receipts, receipts[1:])):
        raise ValueError("Receipt ledger must preserve chronological order")
    protocol_hash, envelope_hash = content_hash(protocol), content_hash(protocol.envelope)
    requirements = {row.gate_id: row for row in protocol.requirements}
    diagnostics, evidence, by_id, superseded = [], [], {}, set()
    for receipt in receipts:
        row = receipt.model_dump(mode="json")
        issues = []
        requirement = requirements[receipt.gate_id]
        expected = {"method_id": protocol.method_id, "runtime_build_hash": protocol.runtime_build_hash,
                    "compiler_build_hash": protocol.compiler_build_hash, "protocol_hash": protocol_hash,
                    "envelope_hash": envelope_hash}
        for key, value in expected.items():
            if row[key] != value:
                issues.append("identity_mismatch:" + key)
        if not any(receipt.scenario_hash == i.scenario_hash and receipt.config_hash == i.config_hash for i in requirement.inputs):
            issues.append("input_identity_outside_frozen_protocol")
        if _time(receipt.recorded_at) < _time(protocol.registered_at):
            issues.append("evidence_precedes_preregistration")
        if receipt.evidence_basis not in REQUIRED_GATES[receipt.gate_id][1]:
            issues.append("unsupported_or_illustrative_evidence_basis")
        try:
            trusted = verify_receipt is not None and verify_receipt(row, content_hash(row)) is True
        except Exception:
            trusted = False
        if not trusted:
            issues.append("receipt_issuer_or_native_provenance_unverified")
        if len(receipt.artifacts) < requirement.minimum_artifacts:
            issues.append("insufficient_actual_artifacts")
        for artifact in receipt.artifacts:
            try:
                raw = read_artifact(artifact.id) if read_artifact is not None else None
                valid = (isinstance(raw, bytes) and len(raw) == artifact.bytes
                         and "sha256:" + hashlib.sha256(raw).hexdigest() == artifact.sha256)
            except Exception:
                valid = False
            if not valid:
                issues.append("artifact_missing_or_integrity_failed:" + artifact.id)
        if receipt.status != "completed":
            issues.append("execution_status:" + receipt.status)
        supplied = {(m.scope, m.name): m for m in receipt.metrics}
        scores = []
        for target in requirement.targets:
            metric = supplied.get((target.scope, target.name))
            passed = metric is not None and metric.unit == target.unit and _compare(metric.value, target)
            scores.append({"target": target.model_dump(mode="json"), "value": metric.value if metric else None, "passed": passed})
            if not passed:
                issues.append("metric_failed_or_missing:" + target.scope + ":" + target.name)
        for previous_id in receipt.supersedes:
            previous = by_id.get(previous_id)
            if (previous is None or previous.gate_id != receipt.gate_id
                    or previous.scenario_hash != receipt.scenario_hash or previous.config_hash != receipt.config_hash
                    or previous.protocol_hash != receipt.protocol_hash):
                issues.append("invalid_remediation_reference:" + previous_id)
        # A failed/untrusted newer receipt cannot erase an earlier failure.
        if not issues:
            superseded.update(receipt.supersedes)
        evidence.append({"receipt": row, "content_hash": content_hash(row), "accepted": not issues,
                         "diagnostics": issues, "metric_checks": scores})
        by_id[receipt.receipt_id] = receipt
    gates = []
    for gate_id, requirement in requirements.items():
        entries = [row for row in evidence if row["receipt"]["gate_id"] == gate_id]
        active = [row for row in entries if row["receipt"]["receipt_id"] not in superseded]
        accepted = [row for row in active if row["accepted"]]
        covered = {(row["receipt"]["scenario_hash"], row["receipt"]["config_hash"]) for row in accepted}
        expected_inputs = {(row.scenario_hash, row.config_hash) for row in requirement.inputs}
        runs = {row["receipt"]["attempt_id"] for row in accepted}
        levels = {row["receipt"]["mesh_level_id"] for row in accepted if row["receipt"]["mesh_level_id"]}
        reasons = []
        if not active:
            reasons.append("no_executed_evidence")
        if any(not row["accepted"] for row in active):
            reasons.append("unresolved_failed_or_unverified_attempt")
        if not expected_inputs <= covered:
            reasons.append("frozen_case_or_regime_coverage_incomplete")
        if len(runs) < requirement.minimum_runs:
            reasons.append("too_few_independent_run_attempts")
        if len(levels) < requirement.minimum_mesh_levels and requirement.minimum_mesh_levels > 1:
            reasons.append("too_few_distinct_mesh_levels")
        gates.append({"gate_id": gate_id, "package": REQUIRED_GATES[gate_id][0], "passed": not reasons,
                      "diagnostics": reasons, "receipt_ids": [row["receipt"]["receipt_id"] for row in entries],
                      "retained_failed_receipt_ids": [row["receipt"]["receipt_id"] for row in entries if not row["accepted"]],
                      "remediated_receipt_ids": [row["receipt"]["receipt_id"] for row in entries if row["receipt"]["receipt_id"] in superseded]})
        if reasons:
            diagnostics.append({"gate_id": gate_id, "package": REQUIRED_GATES[gate_id][0], "reasons": reasons})
    scientific_gates_passed = all(row["passed"] for row in gates)
    packet = {"schema_version": "phase-b-scientific-review-packet-v1", "protocol": protocol.model_dump(mode="json"),
              "protocol_hash": protocol_hash, "envelope_hash": envelope_hash, "gates": gates, "evidence": evidence,
              "scientific_gates_passed": scientific_gates_passed, "diagnostics": diagnostics,
              "limitations": protocol.limitations,
              "review_requirements": ["independent_credentialed_human", "exact_packet_and_envelope_binding",
                                      "all_failure_dispositions", "source_rights_and_QC", "predeclared_protocol_approval",
                                      "release_authority_and_current_not_revoked_signoff"]}
    packet_hash = content_hash(packet)
    try:
        reviewed = (scientific_gates_passed and isinstance(review_reference, str) and bool(review_reference.strip())
                    and verify_review is not None and verify_review(review_reference, packet_hash) is True)
    except Exception:
        reviewed = False
    return {**packet, "packet_hash": packet_hash, "independent_review_verified": reviewed,
            "review_reference": review_reference if reviewed else None, "qualified": reviewed,
            "status": "qualified_for_declared_envelope" if reviewed else
                      ("ready_for_independent_review" if scientific_gates_passed else "unqualified"),
            "B11": "passed" if reviewed else "independent_human_review_pending"}


def scope_admission(packet: dict, *, site_id: str, geometry_family: str, regime: str,
                    process: str, species: str, wind_speed_m_s, receptor_height_m,
                    verify_release: Callable | None = None) -> dict:
    """Evaluate only a trusted stored release packet, never a user upload.

    Qualification packets are host-owned. Callers must verify immutable packet
    identity/current review before this convenience predicate is used.
    """
    try:
        trusted = verify_release is not None and verify_release(packet, content_hash(packet)) is True
    except Exception:
        trusted = False
    if (not trusted or packet.get("qualified") is not True
            or packet.get("independent_review_verified") is not True):
        return {"admitted": False, "reasons": ["no_verified_scientific_release"]}
    envelope = Envelope.model_validate(packet["protocol"]["envelope"])
    reasons = []
    for name, value in (("site_ids", site_id), ("geometry_families", geometry_family), ("regimes", regime),
                        ("processes", process), ("species", species)):
        if value not in getattr(envelope, name):
            reasons.append("outside_envelope:" + name)
    for name, value in (("wind_speed_range_m_s", _number(wind_speed_m_s)),
                        ("receptor_height_range_m", _number(receptor_height_m))):
        low, high = getattr(envelope, name)
        if not low <= value <= high:
            reasons.append("outside_envelope:" + name)
    return {"admitted": not reasons, "reasons": reasons, "envelope_hash": content_hash(envelope)}
