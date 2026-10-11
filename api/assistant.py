"""Bounded actual local Strands assistant; domain tools remain server-authoritative."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from api.assistant_schemas import AssistantMessage, AssistantReply, AssistantStatus, AssistantHistory
from api.assistant_tools import StudyTools, LIMITATIONS, supported_answer
from api.storage import digest, new_id, utc_now

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "assistant"
MODEL = {"provider": "ollama", "model_id": "qwen3:1.7b"}
LIMITS = {"message_characters": 2000, "messages_per_session": 20, "tools_per_message": 6,
          "model_turns": 5, "output_tokens": 1800, "timeout_seconds": 120, "concurrency": 1}
_GATE = threading.Lock()


class LocalModelUnavailable(Exception):
    pass


class LocalModel:
    def status(self):
        evaluation = {"actual_inference_gate": "unavailable", "full_40_task_gate": False,
                      "scope": "Selected-study inspection, geometry, reviewed proposals and matched comparison", "receipt_id": None}
        try:
            receipt = json.loads((RUNTIME / "model-receipt.json").read_text(encoding="utf-8"))
            if receipt.get("inference_gate_passed") is not True or receipt["model_id"] != MODEL["model_id"]:
                raise ValueError("Actual inference gate not accepted")
            evaluation.update(actual_inference_gate="passed", receipt_id=Path(receipt["actual_inference_receipt"]).stem)
            for field in ("evaluated_tasks", "output_guard_accepted", "output_guard_blocked"):
                evaluation[field] = receipt.get(field)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:11435/api/tags", timeout=2) as response:
                tags = json.loads(response.read(65537))
            if not any(row.get("name") == MODEL["model_id"] and row.get("digest") == receipt["manifest_digest"] for row in tags["models"]):
                raise ValueError("Installed model differs from accepted model")
            ready = (RUNTIME / "venv" / "Scripts" / "python.exe").is_file()
        except (OSError, ValueError, KeyError, TypeError):
            ready = False
        busy = ready and _GATE.locked()
        return {"available": ready and not busy, "state": "busy" if busy else "ready" if ready else "unavailable",
                "label": "Study assistant", "model": MODEL, "limits": LIMITS,
                "reason_code": "LOCAL_MODEL_BUSY" if busy else None if ready else "LOCAL_MODEL_UNAVAILABLE",
                "message": "The local model is busy; try again after its current task finishes" if busy else "Local study assistant is ready" if ready else "Local model is unavailable; ordinary study tools remain available",
                "qualified": False, "evaluation": evaluation}

    def infer(self, message, history, tools, timeout=120):
        # Strict allowlist prevents inheriting cloud/database credentials or telemetry destinations.
        environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
        temporary = RUNTIME / "tmp"
        temporary.mkdir(exist_ok=True)
        environment.update({"TEMP": str(temporary), "TMP": str(temporary), "PYTHONUTF8": "1",
                            "PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1", "AWS_EC2_METADATA_DISABLED": "true"})
        process = subprocess.Popen([str(RUNTIME / "venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "assistant_bridge.py")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding="utf-8", cwd=RUNTIME, env=environment,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = queue.Queue(maxsize=12)
        def read():
            try:
                while line := process.stdout.readline(65537):
                    if len(line) > 65536:
                        output.put({"type": "error", "code": "OUTPUT_BOUND"})
                        return
                    output.put(json.loads(line))
            except (ValueError, OSError):
                output.put({"type": "error", "code": "CHANNEL_FAILURE"})
            finally:
                output.put({"type": "closed"})
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        try:
            process.stdin.write(json.dumps({"message": message, "history": history}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            for _ in range(8):
                try:
                    event = output.get(timeout=max(0.001, deadline-time.monotonic()))
                except queue.Empty:
                    raise LocalModelUnavailable("LOCAL_MODEL_TIMEOUT")
                if event.get("type") == "tool":
                    result = tools.invoke(event.get("name"), event.get("arguments"))
                    value = json.dumps({"result": result}, ensure_ascii=False, allow_nan=False)
                    if len(value.encode()) > 60000:
                        raise LocalModelUnavailable("TOOL_RESULT_BOUND")
                    process.stdin.write(value + "\n")
                    process.stdin.flush()
                elif event.get("type") == "answer":
                    return event
                else:
                    raise LocalModelUnavailable("LOCAL_MODEL_FAILED")
            raise LocalModelUnavailable("MODEL_TURN_BOUND")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()


def make_router(*, identity_dependency, store_dependency, required, project_resource,
                authorizer, compare, constraint_validator=None, model=None):
    router = APIRouter(prefix="/api/projects/{project_id}/assistant", tags=["Study assistant"])
    runtime = model or LocalModel()

    def session(store, project_id, identifier, identity):
        value = project_resource(required(store, "assistant_session", identifier, identity), project_id)
        if value["user_id"] != identity.user_id:
            raise HTTPException(404, {"code": "NOT_FOUND", "message": "Session not found"})
        return value

    def records(store, project_id, identifier, identity):
        return sorted((row for row in store.list("assistant_message", identity.tenant_id, project_id)
                       if row["session_id"] == identifier), key=lambda row: row["created_at"])

    @router.get("/status", response_model=AssistantStatus)
    def status(project_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        required(store, "project", project_id, identity)
        return runtime.status()

    @router.get("/sessions/{session_id}", response_model=AssistantHistory)
    def history(project_id: str, session_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        session(store, project_id, session_id, identity)
        return {"id": session_id, "project_id": project_id,
                "messages": [{"message": row["message"], "reply": row["reply"]} for row in records(store, project_id, session_id, identity)]}

    @router.post("/messages", response_model=AssistantReply)
    def message(project_id: str, body: AssistantMessage, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        project = required(store, "project", project_id, identity)
        scenario = project_resource(required(store, "scenario", body.scenario_id, identity), project_id) if body.scenario_id else None
        for identifier in (body.baseline_run_id, body.alternative_run_id):
            if identifier:
                run = store.run(identifier, identity.tenant_id)
                if not run or run["project_id"] != project_id:
                    raise HTTPException(404, {"code": "NOT_FOUND", "message": "Run not found in study"})
        if body.session_id:
            selected = session(store, project_id, body.session_id, identity)
        else:
            sessions = store.list("assistant_session", identity.tenant_id, project_id)
            if len(sessions) >= 200:
                raise HTTPException(429, {"code": "ASSISTANT_PROJECT_LIMIT", "message": "This study's saved assistant allowance is used"})
            selected = {"id": new_id("asession"), "project_id": project_id, "user_id": identity.user_id, "created_at": utc_now()}
        previous = records(store, project_id, selected["id"], identity)
        if len(previous) >= 20:
            raise HTTPException(429, {"code": "ASSISTANT_SESSION_LIMIT", "message": "Start a new session; this session's allowance is used"})
        started = time.monotonic()
        tools = StudyTools(project=project, selected_scenario=scenario, body=body, identity=identity, store=store,
                           authorizer=authorizer, required=required, project_resource=project_resource,
                           compare=compare, constraint_validator=constraint_validator)
        usage = {}
        model_stop_reason = None
        state, answer = "unavailable", "Local model is unavailable. You can continue with the ordinary study tools."
        if runtime.status()["available"] and _GATE.acquire(blocking=False):
            try:
                result = runtime.infer(body.message, [row["message"][:750] for row in previous[-4:]], tools)
                usage = result.get("usage", {})
                model_stop_reason = result.get("stop_reason", "end_turn")
                if model_stop_reason != "end_turn":
                    state, answer = "blocked", "The model reached its turn or token allowance before finishing. Review the retained tool evidence; no edit was applied."
                elif supported_answer(result.get("answer"), tools):
                    state, answer = "complete", result["answer"]
                else:
                    state, answer = "blocked", "The model response did not pass evidence checks. Review the authoritative tool evidence below."
            except (LocalModelUnavailable, OSError, ValueError):
                state, answer = "unavailable", "The local model could not finish within its limits. Any displayed tool evidence is retained; no edit was applied."
            finally:
                _GATE.release()
        reply = {"id": new_id("areply"), "session_id": selected["id"], "status": state, "answer": answer,
                 "evidence": list(tools.evidence.values()), "tool_calls": tools.calls, "proposal": tools.proposal,
                 "limitations": LIMITATIONS, "qualified": False, "model": MODEL, "elapsed_seconds": round(time.monotonic()-started, 3)}
        reply["content_hash"] = digest(reply)
        event = {"id": new_id("amessage"), "project_id": project_id, "session_id": selected["id"],
                 "created_at": utc_now(), "message": body.message, "reply": reply, "usage": usage,
                 "tool_results": tools.results, "authorization_policy": "phase-b-same-project-source-permission"}
        event["tool_inputs"] = tools.inputs
        event["model_stop_reason"] = model_stop_reason
        event["limits"] = LIMITS
        event["prompt_version"] = "study-assistant-20261011-r3"
        with store.transaction() as db:
            # Serial write transaction closes concurrent session-message admission races.
            if len(records(store, project_id, selected["id"], identity)) >= 20:
                raise HTTPException(429, {"code": "ASSISTANT_SESSION_LIMIT", "message": "Session allowance is used"})
            if not body.session_id:
                store.put(db, "assistant_session", identity.tenant_id, selected, project_id)
            store.put(db, "assistant_message", identity.tenant_id, event, project_id)
        return reply

    return router
