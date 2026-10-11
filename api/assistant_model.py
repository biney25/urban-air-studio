"""Fixed local runtime bridge. Model process receives no cloud credentials."""
from pathlib import Path
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "assistant"
HOST = "http://127.0.0.1:11435"
MODEL = "qwen3:1.7b"
SDK_VERSION = "1.59.0"
LIMITS = {"max_message_chars": 2000, "max_turns": 4, "max_tool_calls": 6,
          "timeout_seconds": 45, "max_session_messages": 12, "max_tokens": 600}
PROCESS_LOCK = threading.Lock()


class ModelFailure(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class LocalModel:
    def __init__(self, runtime=RUNTIME):
        self.runtime = Path(runtime)
        self.python = self.runtime / "venv" / "Scripts" / "python.exe"

    @staticmethod
    def _get(path):
        # No environment proxies, redirects, credentials or configurable URL.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(HOST + path, timeout=2) as response:
            if response.geturl() != HOST + path:
                raise ValueError("Local model redirect is forbidden")
            data = response.read(100_001)
            if len(data) > 100_000:
                raise ValueError("Local model metadata is too large")
            return json.loads(data)

    def status(self):
        result = {"available": False, "state": "unavailable", "label": "Study assistant",
                  "model": {"provider": "ollama", "model_id": MODEL}, "limits": LIMITS,
                  "qualified": False, "reason_code": "LOCAL_MODEL_UNAVAILABLE",
                  "message": "Local AI is not ready. You can still edit, study and compare your designs."}
        try:
            receipt_path = self.runtime / "model-receipt.json"
            if not self.python.is_file() or receipt_path.stat().st_size > 20_000:
                return result
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("model_id") != MODEL or receipt.get("strands_version") != SDK_VERSION or receipt.get("actual_tool_loop_passed") is not True:
                return result
            models = self._get("/api/tags").get("models", [])
            loaded = next((row for row in models if row.get("name") == MODEL and row.get("digest") == receipt.get("model_digest")), None)
            if loaded is None:
                return result
            result.update(available=True, state="busy" if PROCESS_LOCK.locked() else "ready", reason_code=None,
                          message="Ask about this study, review a suggested edit, or explain a matched comparison.")
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            pass
        return result

    def run(self, message, history, tools):
        if not self.status()["available"]:
            raise ModelFailure("LOCAL_MODEL_UNAVAILABLE", "Local AI is unavailable; your normal planning tools remain available")
        if not PROCESS_LOCK.acquire(blocking=False):
            raise ModelFailure("ASSISTANT_BUSY", "Another assistant request is running. Try again shortly")
        started = time.monotonic()
        process = None
        try:
            environment = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}}
            environment.update(AWS_EC2_METADATA_DISABLED="true", PYTHONUTF8="1", OTEL_SDK_DISABLED="true", OLLAMA_NO_CLOUD="true")
            process = subprocess.Popen([str(self.python), "-I", str(ROOT / "scripts" / "assistant_model_worker.py")],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       cwd=self.runtime, env=environment, text=True, encoding="utf-8",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            incoming = queue.Queue(maxsize=12)
            def read():
                try:
                    total = 0
                    while line := process.stdout.readline(100_001):
                        total += len(line)
                        if len(line) > 100_000 or total > 300_000:
                            incoming.put({"type": "failure", "code": "MODEL_OUTPUT_BOUND"}, timeout=1)
                            return
                        incoming.put(json.loads(line), timeout=1)
                except (ValueError, OSError, queue.Full):
                    pass
                finally:
                    try:
                        incoming.put({"type": "closed"}, timeout=1)
                    except queue.Full:
                        pass
            threading.Thread(target=read, daemon=True).start()
            process.stdin.write(json.dumps({"message": message, "history": history[-4:], "model": MODEL,
                                           "host": HOST, "limits": LIMITS}) + "\n")
            process.stdin.flush()
            while True:
                remaining = LIMITS["timeout_seconds"] - (time.monotonic() - started)
                if remaining <= 0:
                    raise ModelFailure("ASSISTANT_TIMEOUT", "The local model exceeded its time allowance; no edit was applied")
                try:
                    event = incoming.get(timeout=remaining)
                except queue.Empty:
                    raise ModelFailure("ASSISTANT_TIMEOUT", "The local model exceeded its time allowance; no edit was applied")
                if event.get("type") == "tool":
                    result = tools.invoke(event.get("name"), event.get("arguments"))
                    if len(json.dumps(result)) > 40_000:
                        raise ModelFailure("ASSISTANT_EVIDENCE_BOUND", "Selected study evidence exceeds the assistant allowance")
                    process.stdin.write(json.dumps({"result": result}, allow_nan=False) + "\n")
                    process.stdin.flush()
                elif event.get("type") == "answer":
                    return event.get("answer", "")
                else:
                    raise ModelFailure("ASSISTANT_MODEL_FAILED", "The local model could not complete this request; no edit was applied")
        except (OSError, ValueError, BrokenPipeError) as exc:
            if isinstance(exc, ModelFailure):
                raise
            raise ModelFailure("ASSISTANT_MODEL_FAILED", "The local model could not complete this request; no edit was applied") from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
            PROCESS_LOCK.release()
