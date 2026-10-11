"""Start only the pinned portable loopback model server; do not alter system settings."""
from pathlib import Path
import json
import os
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "assistant"


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:11435/api/version", timeout=2) as response:
            version = json.load(response)["version"]
        if version != "0.40.2":
            raise SystemExit("Existing server differs from the pinned portable version; it was not stopped")
        print("Pinned local model server already responds; no duplicate started")
        return
    except OSError:
        pass
    executable = RUNTIME / "ollama" / "ollama.exe"
    if not executable.exists():
        raise SystemExit("Run setup_local_assistant.py first")
    # Ollama 0.40.2 unconditionally reads its standard identity key. Never relocate,
    # inspect, print or replace it. Generated key is private and is not an AI credential.
    profile = os.environ.get("USERPROFILE")
    if not profile or not (Path(profile) / ".ollama" / "id_ed25519").is_file():
        raise SystemExit("The standard Ollama identity key is unavailable; no profile change was attempted")
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATH") if key in os.environ}
    env.update({"OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(RUNTIME / "models"),
                "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_CONTEXT_LENGTH": "4096",
                "OLLAMA_MAX_QUEUE": "1", "OLLAMA_KEEP_ALIVE": "5m", "OLLAMA_NO_CLOUD": "1"})
    with (RUNTIME / "server.stdout.log").open("ab") as stdout, (RUNTIME / "server.stderr.log").open("ab") as stderr:
        process = subprocess.Popen([str(executable), "serve"], env=env, cwd=RUNTIME, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (RUNTIME / "server-process.json").write_text(json.dumps({"pid": process.pid, "port": 11435, "executable": str(executable)}), encoding="utf-8")
    print("Portable local server started; readiness still requires the accepted actual model receipt")


if __name__ == "__main__":
    main()
