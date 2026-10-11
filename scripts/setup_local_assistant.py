"""Pinned portable local AI runtime. No system/WSL changes or cloud invocation."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "assistant"
VERSION = "0.40.2"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip"
SIZE = 1468085195
SHA = "e29ad1d5063dd4b54b2492d9b00adab2cff9621bfa654b6c92aa8d6f1fdfe7fc"


def main():
    if os.name != "nt":
        raise SystemExit("This exact portable receipt is Windows-only")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(RUNTIME).free < 9_000_000_000:
        raise SystemExit("At least 9 GB free workspace disk is required")
    archive = RUNTIME / f"ollama-{VERSION}-windows-amd64.zip"
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        deadline = time.monotonic() + 1800
        total = 0
        with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Urban-Air-local-runtime"}), timeout=30) as response, temporary.open("wb") as target:
            if int(response.headers.get("Content-Length", SIZE)) != SIZE:
                raise RuntimeError("Release asset length differs from pinned metadata")
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > SIZE or time.monotonic() > deadline:
                    raise RuntimeError("Portable runtime download exceeded its byte/time bound")
                target.write(chunk)
                if total // 100_000_000 != (total - len(chunk)) // 100_000_000:
                    print(f"Downloaded {total // 1_000_000} / {SIZE // 1_000_000} MB", flush=True)
        if total != SIZE:
            raise RuntimeError("Portable runtime download is incomplete")
        temporary.replace(archive)
    sha = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if sha != SHA:
        raise RuntimeError("Portable runtime archive SHA-256 mismatch")
    destination = RUNTIME / "ollama"
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if sum(item.file_size for item in bundle.infolist()) > 8_000_000_000:
            raise RuntimeError("Expanded runtime exceeds fixed disk bound")
        for item in bundle.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Archive path is outside portable runtime")
        bundle.extractall(destination)
    python = RUNTIME / "venv" / "Scripts" / "python.exe"
    if not python.exists():
        subprocess.run([sys.executable, "-m", "venv", str(RUNTIME / "venv")], check=True, timeout=120)
    subprocess.run([str(python), "-m", "pip", "install", "--only-binary=:all:", "--report", str(RUNTIME / "pip-install-report.json"), "strands-agents[ollama]==1.59.0"], check=True, timeout=600)
    freeze = subprocess.run([str(python), "-m", "pip", "freeze"], check=True, capture_output=True, text=True).stdout
    (RUNTIME / "requirements-installed.txt").write_text(freeze, encoding="utf-8")
    receipt = {"ollama_version": VERSION, "archive_sha256": sha, "source": URL,
               "strands_version": "1.59.0", "provider": "ollama", "cloud_deployed": False,
               "spend_currency": 0, "system_configuration_changed": False}
    (RUNTIME / "installation-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("Pinned portable runtime installed; actual inference has not yet been accepted", flush=True)


if __name__ == "__main__":
    main()
