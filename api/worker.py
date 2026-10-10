"""Separate durable local worker. Run with python -m api.worker (loopback adapter)."""
import argparse
import json
import math
import os
import platform
import time
import uuid

from api.storage import Store, digest, utc_now


class DeadlineError(Exception):
    pass


def verify_result(result):
    field = result["field"]
    if field["schema_version"] != "fields-v1" or field["units"] != "ug/m3":
        raise ValueError("Unsupported field contract")
    if len(field["values"]) != field["nx"]*field["ny"] or len(field["mask"]) != len(field["values"]):
        raise ValueError("Field shape mismatch")
    for value, valid in zip(field["values"], field["mask"]):
        if not valid and value is not None:
            raise ValueError("Masked cell must have null concentration")
        if value is None:
            if valid:
                raise ValueError("Valid cell cannot have null concentration")
        elif not isinstance(value,(float,int)) or not math.isfinite(value) or value < -1e-9:
            raise ValueError("Invalid physical field value")
    for metric in result["metrics"]:
        value = metric.get("value_ug_m3")
        if metric.get("valid") and (value is None or not math.isfinite(value) or value < -1e-9):
            raise ValueError("Invalid receptor metric")
    if result["qualification"] != "research_preview" or result["evidence_kind"] != "simulated":
        raise ValueError("Unsupported scientific evidence status")
    if result["diagnostics"].get("numerical_verification_passed") is not True:
        raise ValueError("Scientific numerical gates have not passed")


def work_once(store=None, worker_id=None, simulate_fn=None, before_publish=None):
    """Process one claim; injection arguments are for meaningful fault tests only."""
    from science import MODEL_VERSION, SCIENCE_BUILD_HASH, SCIENCE_SOURCE_HASHES, DEPENDENCY_VERSIONS, simulate
    from science.geometry import ScienceError
    store = store or Store()
    if os.getenv("UA_MODE", "local") != "local" and store.adapter_name != "postgres-postgis-s3":
        raise RuntimeError("Public deployment worker requires PostgreSQL/PostGIS and private S3 storage")
    worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    job = store.claim(worker_id)
    if not job:
        return False
    start = time.monotonic()
    run_id, fence = job["id"], job["_fence"]
    allowance = job["manifest"]["resource_allowance_seconds"]
    last_heartbeat = 0

    def cancelled():
        nonlocal last_heartbeat
        if time.monotonic()-start > allowance:
            raise DeadlineError("Local worker wall-time allowance exhausted")
        current = store.run(run_id)
        if current is None or current["cancel_requested"] or current["_fence"] != fence:
            return True
        if time.monotonic()-last_heartbeat >= 2:
            last_heartbeat = time.monotonic()
            return not store.heartbeat(run_id,fence)
        return False

    try:
        from api.preview_scope import inventory_bound,CODE,MESSAGE
        if inventory_bound(job["manifest"]["inputs"]):
            store.finish(run_id,fence,"failed",{"code":CODE,"message":MESSAGE},seconds=time.monotonic()-start)
            return True
        if (job["manifest"]["model_version"] != MODEL_VERSION or job["manifest"].get("science_build_hash") != SCIENCE_BUILD_HASH
            or job["manifest"].get("science_source_hashes") != SCIENCE_SOURCE_HASHES
            or job["manifest"].get("dependency_versions") != DEPENDENCY_VERSIONS):
            store.finish(run_id,fence,"failed", {"code":"MODEL_VERSION_MISMATCH","message":"Queued method differs from installed worker; retain/reinstall pinned method"})
            return True
        if not store.transition(run_id,fence,"solving",0.2) or cancelled():
            store.finish(run_id,fence,"cancelled",seconds=time.monotonic()-start)
            return True
        result = (simulate_fn or simulate)(job["manifest"]["inputs"],job["manifest"]["conditions"],cancel_check=cancelled)
        result["limitations"] = list(dict.fromkeys(result["limitations"] + job["manifest"]["limitations"]))
        if not store.transition(run_id,fence,"verifying",0.85) or cancelled():
            store.finish(run_id,fence,"cancelled",seconds=time.monotonic()-start)
            return True
        verify_result(result)
        artifact_manifest = {"schema_version":"phase-a-artifact-v1","run_id":run_id,"attempt":job["attempt"],
                             "fencing_token":fence,"created_at":utc_now(),"scenario_hash":job["manifest"]["scenario_hash"],
                             "result_hash":digest(result),"input_manifest_hash":digest(job["manifest"]),
                             "model_version":MODEL_VERSION,"runtime":{"python":platform.python_version(),
                             "science_build_hash":SCIENCE_BUILD_HASH,"science_source_hashes":SCIENCE_SOURCE_HASHES,
                             "dependency_versions":DEPENDENCY_VERSIONS,
                             "platform":platform.system(),"architecture":platform.machine(),"adapter":store.adapter_name},
                             "verification":"model numerical gates plus field/metric contract passed"}
        relative, checksum = store.atomic_artifact(run_id,fence,{"result":result,"artifact_manifest":artifact_manifest})
        # Race-test hook is called after durable files and before the sole authoritative DB commit.
        if before_publish:
            before_publish(job)
        store.finish(run_id,fence,"complete_unqualified",artifact=relative,seconds=time.monotonic()-start,artifact_checksum=checksum)
    except ScienceError as exc:
        status = "cancelled" if exc.code == "RUN_CANCELLED" else "failed"
        store.finish(run_id,fence,status,{"code":exc.code,"message":exc.message,"issues":exc.issues},seconds=time.monotonic()-start)
    except DeadlineError:
        store.finish(run_id,fence,"failed",{"code":"BUDGET_EXCEEDED","message":"Local wall-time allowance exhausted; no result was published"},seconds=time.monotonic()-start)
    except OSError:
        store.finish(run_id,fence,"failed",{"code":"LOCAL_IO_ERROR","message":"Artifact storage failure; no result published"},seconds=time.monotonic()-start)
    except Exception as exc:
        print(json.dumps({"run_id":run_id,"event":"worker_failed","exception_type":type(exc).__name__}),flush=True)
        store.finish(run_id,fence,"failed",{"code":"VERIFICATION_FAILED","message":"Result contract or execution failed; no completed result published"},seconds=time.monotonic()-start)
    return True


def main():
    parser = argparse.ArgumentParser(description="Urban Air Studio separate durable research worker")
    parser.add_argument("--once",action="store_true",help="Process one durable job then exit")
    parser.add_argument("--poll-seconds",type=float,default=1)
    args = parser.parse_args()
    mode = os.getenv("UA_MODE","local")
    if mode not in {"local","cognito"} or (mode == "cognito" and not (os.getenv("UA_DATABASE_URL") and os.getenv("UA_ASSET_BUCKET"))):
        raise SystemExit("Worker identity/storage is not configured")
    try:
        store = Store()
        if mode == "cognito" and store.adapter_name != "postgres-postgis-s3":
            raise ValueError("Incorrect public worker adapter")
    except Exception as exc:
        print(json.dumps({"event":"worker_startup_failed","code":"PLATFORM_NOT_READY","exception_type":type(exc).__name__}),flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"event":"worker_started","adapter":store.adapter_name,"pid":os.getpid()}),flush=True)
    while True:
        worked = work_once(store)
        if args.once:
            break
        if not worked:
            time.sleep(max(0.1,args.poll_seconds))


if __name__ == "__main__":
    main()
