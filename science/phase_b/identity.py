"""Capture source identity once at import; later disk edits cannot attest code."""
import hashlib
import json
from pathlib import Path
import platform

import pydantic
import pyproj
import shapely

COMPILER_VERSION = "phase-b-foundation13-compiler-1.2"
METHOD_ID = "foundation13-neutral-rans-passive-scalar-preparation-v1"
COMMIT = "18870c24d21c6b982e2cdec27b2f59738cca5f90"


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ("__init__.py", "config.py", "geometry.py", "dictionaries.py", "runner.py", "compiler.py", "identity.py", "source_audit.py", "requirements.txt")
SOURCE_HASHES = {name: "sha256:" + hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}
AUDIT_BYTES = (ROOT/"official-source-audit.json").read_bytes()
SOURCE_AUDIT_HASH = "sha256:" + hashlib.sha256(AUDIT_BYTES).hexdigest()
AUDIT = json.loads(AUDIT_BYTES)
if AUDIT.get("source_commit") != COMMIT or AUDIT.get("distribution") != "OpenFOAM Foundation" or not AUDIT.get("files") or any(f.get("retrieved") is not True for f in AUDIT["files"]):
    raise RuntimeError("Foundation 13 official source audit is incomplete or differs from the pinned distribution")
DEPENDENCIES = {"python": platform.python_version(), "pydantic": pydantic.__version__, "shapely": shapely.__version__,
                "GEOS": shapely.geos_version_string, "pyproj": pyproj.__version__, "PROJ": pyproj.proj_version_str}
COMPILER_BUILD_HASH = digest({"source_hashes": SOURCE_HASHES, "source_audit_hash": SOURCE_AUDIT_HASH,
                              "compiler_version": COMPILER_VERSION, "dependencies": DEPENDENCIES})
