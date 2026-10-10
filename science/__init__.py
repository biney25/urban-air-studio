"""Bounded, explicitly unvalidated Phase A scientific research tools."""

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

# Capture the running process's method identity ONCE. Reading the files again
# after an edit would falsely attest that an old imported worker runs new code.
_package_root = Path(__file__).resolve().parent
SCIENCE_SOURCE_HASHES = {
    f"science/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(_package_root.glob("*.py"), key=lambda path: path.name)
}
SCIENCE_BUILD_HASH = hashlib.sha256(
    json.dumps(SCIENCE_SOURCE_HASHES, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
DEPENDENCY_VERSIONS = {name: version(name) for name in ("numpy", "scipy", "shapely", "pyproj")}

from .errors import ScienceError
from .model import MODEL_VERSION, simulate

__all__ = ["MODEL_VERSION", "SCIENCE_BUILD_HASH", "SCIENCE_SOURCE_HASHES", "DEPENDENCY_VERSIONS", "ScienceError", "simulate"]
