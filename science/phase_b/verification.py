"""Preregistered diagnostic ladder evaluation; never physical qualification."""
from copy import deepcopy
import hashlib
import json
import math


def digest(value):
    return "sha256:"+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _finite(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError("Diagnostic metric must be a finite physical number")
    return float(value)


def verify_protocol(protocol):
    value=deepcopy(protocol)
    checksum=value.pop("content_hash",None)
    if value.get("schema_version")!="phase-b-numerical-ladder-protocol-v1" or value.get("qualified") is not False or checksum!=digest(value):
        raise ValueError("Frozen numerical protocol failed its identity check")
    targets={"relative_mass_balance_strict":.01,"relative_stationarity":.01,"mesh_domain_relative":.05,
             "uniform_background_relative":1e-6,"source_linearity_relative":.001,"no_change_relative":1e-10}
    if value.get("targets")!=targets or any(isinstance(v,bool) for v in value["targets"].values()):
        raise ValueError("Versioned preregistered numerical targets cannot be relaxed")
    identifiers=[item["id"] for item in value["cases"]]
    if not identifiers or len(identifiers)!=len(set(identifiers)):
        raise ValueError("Preregistered native case IDs must be unique")
    kinds={"no_change":2,"source_linearity":2,"source_control":2,"mesh_sensitivity":3,
           "domain_sensitivity":3,"transport_parameter_sensitivity":3}
    for comparison in value["comparisons"]:
        kind,names=comparison.get("kind"),comparison.get("cases",[])
        if kind not in kinds or len(names)!=kinds[kind] or len(set(names))!=len(names) or set(names)-set(identifiers):
            raise ValueError("Preregistered comparison needs the declared number of distinct registered cases")
    return protocol


def _checked_receipt(protocol,case,receipt):
    if not isinstance(receipt,dict) or receipt.get("schema_version")!="phase-b-native-execution-v1" or receipt.get("qualified") is not False:
        raise ValueError("Native numerical attempt has unsupported identity")
    attestation=receipt.get("attestation",{})
    if (attestation.get("execution_id")!=case["execution_id"] or attestation.get("fence")!=1
            or attestation.get("case_bundle_checksum")!=case["bundle_checksum"]
            or attestation.get("runtime_digest")!=protocol["runtime_digest"]):
        raise ValueError("Native attempt differs from preregistered case/runtime")
    value=deepcopy(receipt)
    checksum=value["attestation"].pop("receipt_content_hash",None)
    if checksum!=digest(value):
        raise ValueError("Native numerical receipt content was changed")
    return receipt


def _metrics(receipt):
    if receipt.get("status")!="executed_unqualified" or receipt.get("analysis",{}).get("limited_checks_passed") is not True:
        raise ValueError("Failed or incomplete native run cannot support an accepted numerical comparison")
    if receipt["analysis"].get("C_unit")!="kg/m3" or receipt["analysis"].get("reported_C_unit")!="ug/m3":
        raise ValueError("Native comparison scalar units are unmatched")
    rows=receipt["analysis"]["receptors"]
    if not isinstance(rows,list) or not rows or len({row["id"] for row in rows})!=len(rows):
        raise ValueError("Native comparison requires unique actual receptor support")
    return {row["id"]:{"local_increment_kg_m3":_finite(row["local_increment_ug_m3"])*1e-9,
                       "speed_m_s":_finite(row["speed_m_s"]),"total_ug_m3":_finite(row["C_ug_m3"])} for row in rows}


def _relative_changes(left,right):
    if set(left)!=set(right):
        raise ValueError("Native comparisons need identical frozen receptor IDs")
    rows=[]
    for identifier in sorted(left):
        for metric,floor in (("local_increment_kg_m3",1e-18),("speed_m_s",1e-12)):
            a,b=left[identifier][metric],right[identifier][metric]
            rows.append({"receptor_id":identifier,"metric":metric,"left":a,"right":b,
                         "absolute_change":b-a,"relative_change":abs(b-a)/max(abs(b),floor)})
    return rows


def evaluate_ladder(protocol,receipts):
    """Retain failed/missing cases and every predeclared comparison/regime.

    Arithmetic targets apply only to this artificial neutral/passive test
    fixture. Passing them cannot admit measured replay, scientific validity,
    city-wide accuracy or the production qualification envelope.
    """
    verify_protocol(protocol)
    cases={item["id"]:item for item in protocol["cases"]}
    if set(receipts)-set(cases):
        raise ValueError("Unregistered numerical cases cannot enter the frozen ladder")
    results,metrics,checked={},{},{}
    for identifier,case in cases.items():
        receipt=receipts.get(identifier)
        if receipt is None:
            results[identifier]={"state":"pending","qualified":False}
            continue
        try:
            _checked_receipt(protocol,case,receipt)
            checked[identifier]=receipt
            metrics[identifier]=_metrics(receipt)
            results[identifier]={"state":"diagnostic_pass","qualified":False,"receipt_sha256":digest(receipt),
                                 "runtime_seconds":receipt.get("resources",{}).get("wall_seconds"),
                                 "cell_count":receipt.get("mesh",{}).get("cell_count"),
                                 "limited_gates":deepcopy(receipt["analysis"]["gates"])}
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            results[identifier]={"state":"failed","qualified":False,"receipt_sha256":digest(receipt),
                                 "reason":str(exc),"native_status":receipt.get("status"),
                                 "limited_gates":deepcopy(receipt.get("analysis",{}).get("gates",{})),
                                 "issues":deepcopy(receipt.get("diagnostics",{}).get("issues",[]))}
    comparisons=[]
    for spec in protocol["comparisons"]:
        names=spec["cases"]
        row={"id":spec["id"],"kind":spec["kind"],"cases":names,"qualified":False,"state":"inconclusive"}
        if any(name not in metrics for name in names):
            row["reason"]="Required actual native case is missing or failed its own numerical diagnostic"
            comparisons.append(row)
            continue
        baseline=metrics[names[0]]
        try:
            details=[]
            if spec["kind"]=="no_change":
                target=protocol["targets"]["no_change_relative"]
                details=_relative_changes(baseline,metrics[names[1]])
                passed=all(item["relative_change"]<=target for item in details)
            elif spec["kind"]=="source_linearity":
                target=protocol["targets"]["source_linearity_relative"]
                after=metrics[names[1]]
                if set(baseline)!=set(after): raise ValueError("Source test support changed")
                for identifier in baseline:
                    expected=2*baseline[identifier]["local_increment_kg_m3"]
                    value=after[identifier]["local_increment_kg_m3"]
                    error=abs(value-expected)/max(abs(expected),1e-18)
                    details.append({"receptor_id":identifier,"metric":"local_increment_kg_m3","expected":expected,"actual":value,"relative_error":error})
                passed=all(item["relative_error"]<target for item in details)
            elif spec["kind"]=="source_control":
                target=protocol["targets"]["uniform_background_relative"]
                after=metrics[names[1]]
                if set(baseline)!=set(after): raise ValueError("Source-control support changed")
                for identifier in baseline:
                    reference=baseline[identifier]["total_ug_m3"]
                    value=after[identifier]["total_ug_m3"]
                    details.append({"receptor_id":identifier,"before_total_ug_m3":reference,"after_total_ug_m3":value,
                                    "delta_ug_m3":value-reference,"protected_no_harm":value<=reference+1e-9})
                passed=all(item["protected_no_harm"] for item in details)
            else:
                target=protocol["targets"]["mesh_domain_relative"]
                for a,b in zip(names,names[1:]):
                    details.extend({"pair":[a,b],**item} for item in _relative_changes(metrics[a],metrics[b]))
                passed=all(item["relative_change"]<target for item in details)
            row.update(state="passed" if passed else "failed",target=target,details=details,
                       claims="Artificial diagnostic response only; no physical intervention/city accuracy claim")
        except (ValueError,TypeError,KeyError) as exc:
            row.update(state="failed",reason=str(exc))
        comparisons.append(row)
    states=[row["state"] for row in results.values()]+[row["state"] for row in comparisons]
    overall="failed" if "failed" in states else "pending" if any(state in {"pending","inconclusive"} for state in states) else "diagnostic_ladder_passed"
    value={"schema_version":"phase-b-numerical-ladder-results-v1","protocol_sha256":digest(protocol),"state":overall,
           "qualified":False,"full_numerical_verification_passed":False,"cases":results,"comparisons":comparisons,
           "unexecuted_required_gates":["independent_manufactured_transport","whole_field_stationarity","empty_domain_ABL_wall_support",
               "independent_flow_and_tracer_benchmarks","RANS_closure_sensitivity","held_out_measured_replay_uncertainty","independent_review"],
           "limitations":["All inputs are original illustrative numerical fixtures.",
                          "Sc_t sensitivity is transport-parameter sensitivity, not turbulence-closure sensitivity.",
                          "Cell-interpolated common receptor metrics need resolution sensitivity; no observed interval means are inferred."]}
    value["content_hash"]=digest(value)
    return value
