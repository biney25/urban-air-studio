"""Bounded native Foundation13 diagnostic execution; never scientific admission.

This module and its CLI use the standard library, so a trusted WSL bridge can
invoke the exact same source without importing the Windows compiler runtime.
The application must regenerate/check its stored compiler bundle before calling
the bridge. Runtime receipt, native working root and executable paths are
operator configuration; none may come from a browser request.
"""
from __future__ import annotations

import argparse
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import subprocess
import time

PINNED_COMMIT = "18870c24d21c6b982e2cdec27b2f59738cca5f90"
SCHEMA = "phase-b-native-execution-v1"
COMMANDS = ("blockMesh", "snappyHexMesh", "checkMesh", "topoSet", "setAtmBoundaryLayer", "uaMeshAudit", "foamRun")
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_MESH_FILE_BYTES = 64 * 1024 * 1024
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_RSS_BYTES = 6 * 1024 * 1024 * 1024
EXECUTOR_HASH = "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
ALLOWED_FILES = {
    "README.md", "0/U", "0/p", "0/k", "0/epsilon", "0/nut", "0/C",
    "constant/physicalProperties", "constant/momentumTransport", "constant/fvModels", "constant/fvConstraints",
    "system/blockMeshDict", "system/meshQualityDict", "system/snappyHexMeshDict", "system/topoSetDict",
    "system/setAtmBoundaryLayerDict", "system/fvSchemes", "system/fvSolution", "system/controlDict",
}
ALLOWED_TYPES = {
    "triSurface", "box", "patch", "wall", "symmetryPlane", "cellSet", "cellZoneSet",
    "atmBoundaryLayerInletVelocity", "atmBoundaryLayerInletK", "atmBoundaryLayerInletEpsilon",
    "pressureInletOutletVelocity", "noSlip", "fixedValue", "zeroGradient", "inletOutlet",
    "kqRWallFunction", "epsilonWallFunction", "calculated", "nutkAtmRoughWallFunction", "nutkWallFunction",
    "semiImplicitSource", "scalarTransport", "probes", "fieldMinMax", "volFieldValue", "surfaceFieldValue", "yPlus",
}
ALLOWED_LIBS = {"libsolverFunctionObjects.so", "libsampling.so", "libfieldFunctionObjects.so", "libatmosphericModels.so"}


class ExecutionError(ValueError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}


def _fail(code, message, **details):
    raise ExecutionError(code, message, details)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def _number(value, what, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("EXECUTION_INPUT_INVALID", f"{what} must be a finite physical number")
    try:
        result = float(value)
    except OverflowError:
        result = float("inf")
    if not math.isfinite(result) or (positive and result <= 0):
        _fail("EXECUTION_INPUT_INVALID", f"{what} must be finite" + (" and positive" if positive else ""))
    return result


def _integer(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        _fail("EXECUTION_INPUT_INVALID", f"{what} exceeds its integer bounds")
    return value


def _json_file(path, max_bytes):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        _fail("EXECUTION_INPUT_INVALID", "Controlled JSON file is missing, linked or oversized")
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    _fail("EXECUTION_INPUT_INVALID", "Duplicate JSON keys are not admitted")
                result[key] = value
            return result
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: _fail("EXECUTION_INPUT_INVALID", "Nonfinite JSON is not admitted"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("EXECUTION_INPUT_INVALID", "Controlled JSON file is not strict UTF-8 JSON")


def _real_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        _fail("EXECUTION_PATH_INVALID", "Runtime and working roots must be real absolute directories")
    # Refuse a symlink in any existing parent, including an alias to another root.
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        _fail("EXECUTION_PATH_INVALID", "Linked parent directories are not admitted")
    return path.resolve(strict=True)


@dataclass(frozen=True)
class RuntimeIdentity:
    root: Path
    environment: dict
    commands: dict
    files: dict
    digest: str
    provenance: dict


def verify_runtime(receipt):
    """Verify locally installed bytes and pinned source, not a remote assertion.

    Build provenance is retained as a declared operator build receipt. Byte/hash
    checks bind that receipt to what executes; they are not independent binary
    reproducibility, physical validation or a security sandbox certification.
    """
    if platform.system() != "Linux" or os.name != "posix":
        _fail("DETAILED_RUNTIME_UNAVAILABLE", "Detailed execution requires the verified native Linux worker")
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "phase-b-native-runtime-v1":
        _fail("RUNTIME_ATTESTATION_INVALID", "Native runtime receipt schema is unsupported")
    if (receipt.get("distribution") != "OpenFOAM Foundation" or str(receipt.get("version")) != "13"
            or receipt.get("source_commit") != PINNED_COMMIT or receipt.get("clean_tree") is not True):
        _fail("RUNTIME_ATTESTATION_INVALID", "Only the clean pinned Foundation13 source build is admitted")
    root = _real_directory(receipt.get("foam_root", ""))
    source_root = _real_directory(receipt.get("source_root", str(root)))
    env = receipt.get("environment")
    commands, files = receipt.get("commands"), receipt.get("files")
    if not isinstance(env, dict) or not isinstance(commands, dict) or not isinstance(files, dict):
        _fail("RUNTIME_ATTESTATION_INVALID", "Runtime environment/executable/file identities are required")
    if not files or len(files) > 4096 or set(commands) != set(COMMANDS):
        _fail("RUNTIME_ATTESTATION_INVALID", "Fixed native applications and bounded runtime file identities are required")
    if env.get("WM_PROJECT_VERSION") != "13" or env.get("WM_PROJECT_DIR") != str(root):
        _fail("RUNTIME_ATTESTATION_INVALID", "Runtime environment differs from the source root/version")
    allowed_env = {"PATH", "LD_LIBRARY_PATH", "WM_PROJECT_DIR", "WM_PROJECT_VERSION", "WM_OPTIONS", "WM_PRECISION_OPTION",
                   "WM_LABEL_SIZE", "WM_MPLIB", "FOAM_APPBIN", "FOAM_LIBBIN", "FOAM_EXT_LIBBIN", "FOAM_ETC", "HOME",
                   "WM_PROJECT_USER_DIR", "FOAM_USER_APPBIN", "FOAM_USER_LIBBIN", "MPI_ARCH_PATH", "OPAL_PREFIX"}
    if set(env) - allowed_env or any(not isinstance(v, str) or "\x00" in v or len(v) > 8192 for v in env.values()):
        _fail("RUNTIME_ATTESTATION_INVALID", "Runtime environment contains unsupported values or overrides")
    if not env.get("PATH") or not env.get("FOAM_APPBIN") or not env.get("FOAM_LIBBIN"):
        _fail("RUNTIME_ATTESTATION_INVALID", "Native executable and library roots are missing")
    for path_text, expected in files.items():
        path = Path(path_text)
        if not path.is_absolute() or not isinstance(expected, str) or not HASH.fullmatch(expected):
            _fail("RUNTIME_ATTESTATION_INVALID", "Runtime hashes must identify absolute local files")
        # Native build products may have an audited symlink; the digest binds its
        # final target, while all relative case inputs/outputs forbid symlinks.
        if not path.is_file() or _file_hash(path) != expected:
            _fail("RUNTIME_INTEGRITY_ERROR", "Installed native executable/library/source bytes changed")
    native_libs = set()
    for key in ("FOAM_LIBBIN", "FOAM_EXT_LIBBIN", "FOAM_USER_LIBBIN"):
        if env.get(key):
            libroot = Path(env[key])
            if not libroot.is_absolute() or key == "FOAM_LIBBIN" and not libroot.is_dir():
                _fail("RUNTIME_ATTESTATION_INVALID", "Declared native library root is missing")
            native_libs.update(str(p) for p in libroot.rglob("*.so*") if p.is_file())
    if not native_libs or native_libs - set(files):
        _fail("RUNTIME_ATTESTATION_INVALID", "Every runtime library in configured OpenFOAM roots must be hashed")
    for name, path_text in commands.items():
        path = Path(path_text)
        if path_text not in files or path.name != name or not os.access(path, os.X_OK):
            _fail("RUNTIME_ATTESTATION_INVALID", "Fixed executable is absent from the installed-byte receipt")
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                _fail("RUNTIME_ATTESTATION_INVALID", "Only native ELF applications are admitted; scripts are not")
    git = Path("/usr/bin/git")
    if not git.is_file():
        _fail("RUNTIME_ATTESTATION_INVALID", "Installed source identity cannot be checked")
    try:
        head = subprocess.run([str(git), "rev-parse", "HEAD"], cwd=source_root, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run([str(git), "diff", "--quiet", "HEAD", "--"], cwd=source_root,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=10).returncode
    except (subprocess.SubprocessError, OSError):
        _fail("RUNTIME_ATTESTATION_INVALID", "Installed source identity check failed")
    if head != PINNED_COMMIT or dirty != 0:
        _fail("RUNTIME_INTEGRITY_ERROR", "Installed source revision or tracked bytes changed")
    audit_path = Path(__file__).with_name("official-source-audit.json")
    audit = _json_file(audit_path, 1024 * 1024)
    if audit.get("source_commit") != PINNED_COMMIT:
        _fail("RUNTIME_ATTESTATION_INVALID", "Executor official source audit differs from the method pin")
    for item in audit["files"]:
        if _file_hash(source_root / item["path"]) != "sha256:" + item["sha256"]:
            _fail("RUNTIME_INTEGRITY_ERROR", "Audited official source bytes differ from the installed build")
        if source_root != root and _file_hash(root / item["path"]) != "sha256:" + item["sha256"]:
            _fail("RUNTIME_INTEGRITY_ERROR", "Packaged audited source bytes differ from the pinned source tree")
    if source_root != root:
        package = receipt.get("package_provenance", {})
        if (package.get("package_sha256") != "sha256:ed083f8b5ad7b8e8e5f46a01bd71ba31857c7d9572d0875496e41a7683241b4e"
                or package.get("source_comparison_passed") is not True
                or package.get("binary_build_commit") != PINNED_COMMIT
                or not isinstance(package.get("comparison_receipt_sha256"), str)
                or not HASH.fullmatch(package["comparison_receipt_sha256"])):
            _fail("RUNTIME_ATTESTATION_INVALID", "Separated official package/source roots require exact package, source comparison and binary build identity")
    helper = Path(__file__).parent / "native" / "uaMeshAudit"
    for relative in ("uaMeshAudit.C", "Make/files", "Make/options"):
        path = helper / relative
        if files.get(str(path.resolve())) != _file_hash(path):
            _fail("RUNTIME_ATTESTATION_INVALID", "Original controlled native audit source/build inputs must be hash-bound")
    effective = dict(env)
    effective.update({"LANG": "C", "LC_ALL": "C", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "1",
                      "AWS_EC2_METADATA_DISABLED": "true"})
    public_identity = {"distribution": "OpenFOAM Foundation", "version": "13", "source_commit": PINNED_COMMIT,
                       "third_party_commit": receipt.get("third_party_commit"), "receipt_sha256": _digest(receipt),
                       "executor_sha256": EXECUTOR_HASH, "native_binary_hashes": {name: files[path] for name, path in sorted(commands.items())},
                       "build_sha256": _digest(receipt.get("build", {}))}
    return RuntimeIdentity(root, effective, dict(commands), dict(files), _digest(public_identity),
                           {"distribution": "OpenFOAM Foundation", "version": "13", "source_commit": PINNED_COMMIT,
                            "third_party_commit": receipt.get("third_party_commit"), "build": receipt.get("build", {}),
                            "runtime_identity": public_identity,
                            "runtime_receipt_hash": _digest(receipt), "binary_reproducibility_independently_verified": False})


def runtime_readiness(*, receipt_path=None):
    path = receipt_path or os.environ.get("UA_FOAM_RUNTIME_RECEIPT")
    if not path:
        return {"available": False, "state": "runtime_not_configured", "qualified": False}
    try:
        runtime = verify_runtime(_json_file(path, 4 * 1024 * 1024))
        return {"available": True, "state": "native_runtime_verified", "runtime_digest": runtime.digest,
                "executor_hash": EXECUTOR_HASH, "qualified": False,
                "runtime_identity": runtime.provenance["runtime_identity"]}
    except (ExecutionError, OSError, KeyError, TypeError, ValueError) as exc:
        return {"available": False, "state": "runtime_verification_failed", "qualified": False,
                "error_code": exc.code if isinstance(exc, ExecutionError) else "RUNTIME_ATTESTATION_INVALID"}


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get("manifest"), dict) or not isinstance(bundle.get("files"), dict):
        _fail("CASE_INTEGRITY_ERROR", "An immutable compiler-produced bundle and manifest are required")
    manifest, files = bundle["manifest"], bundle["files"]
    if manifest.get("schema_version") != "phase-b-case-v1" or manifest.get("method", {}).get("source_commit") != PINNED_COMMIT:
        _fail("CASE_INTEGRITY_ERROR", "Prepared case differs from the pinned method")
    if manifest.get("qualified") is not False or not 1 <= len(files) <= 1000 or set(files) != set(manifest.get("file_hashes", {})) or not ALLOWED_FILES <= set(files):
        _fail("CASE_INTEGRITY_ERROR", "Prepared file identities or qualification status are invalid")
    total = 0
    for name, text in files.items():
        if (not isinstance(name, str) or not isinstance(text, str) or name not in ALLOWED_FILES
                and not re.fullmatch(r"constant/geometry/building_[0-9]{4}\.obj", name)):
            _fail("CASE_PATH_INVALID", "Only original compiler dictionary/field/surface paths are admitted")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")):
            _fail("CASE_PATH_INVALID", "Prepared case path escapes its controlled root")
        content = text.encode("utf-8")
        total += len(content)
        if manifest["file_hashes"][name] != "sha256:" + hashlib.sha256(content).hexdigest():
            _fail("CASE_INTEGRITY_ERROR", "Prepared case text differs from its frozen checksum")
        if name == "README.md" or name.endswith(".obj"):
            continue
        clean = _strip_comments(text)
        if "#" in clean or re.search(r"\b(?:coded\w*|code\w*|systemCall|fileName|executeCommand)\b", clean):
            _fail("CASE_CODE_FORBIDDEN", "Includes, dynamic code and user commands are not permitted")
        for object_type in re.findall(r"\btype\s+([^;]+);", clean):
            if object_type.strip() not in ALLOWED_TYPES:
                _fail("CASE_CODE_FORBIDDEN", "Dictionary selects an unreviewed dynamic object")
        for entry in re.findall(r"\blibs\b([^;]+);", clean, flags=re.S):
            if not re.fullmatch(r'\s*\(\s*(?:"[^"\n]+"\s*)*\)\s*', entry):
                _fail("CASE_CODE_FORBIDDEN", "Only reviewed native function-object libraries may be loaded")
            libs = entry.strip()[1:-1]
            if set(re.findall(r'"([^"\n]+)"', libs)) - ALLOWED_LIBS or re.sub(r'"[^"\n]+"\s*', "", libs).strip():
                _fail("CASE_CODE_FORBIDDEN", "Only reviewed native function-object libraries may be loaded")
        for entry in re.findall(r"\bfile\s+([^;]+);", clean):
            surface = re.fullmatch(r'"(building_[0-9]{4}\.obj)"', entry.strip())
            if not surface or "constant/geometry/"+surface[1] not in files:
                _fail("CASE_CODE_FORBIDDEN", "Surface files must be compiler-controlled bundled geometry")
    if total != manifest.get("bundle_bytes") or total > MAX_INPUT_BYTES:
        _fail("CASE_INTEGRITY_ERROR", "Prepared byte count exceeds or differs from its frozen contract")
    config = manifest.get("config", {})
    _integer(config.get("max_cells"), "Cell ceiling", 1000, 10_000_000)
    _integer(config.get("max_wall_seconds"), "Wall ceiling", 60, 86400)
    _integer(config.get("flow_iterations"), "Iteration ceiling", 100, 10000)
    if "config" in bundle and _digest(bundle["config"]) != manifest.get("config_hash"):
        _fail("CASE_INTEGRITY_ERROR", "Resolved configuration differs from the frozen manifest")
    if "scenario" in bundle and _digest(bundle["scenario"]) != manifest.get("scenario_hash"):
        _fail("CASE_INTEGRITY_ERROR", "Scenario differs from the frozen compiler manifest")
    control = _strip_comments(files.get("system/controlDict", ""))
    if not re.search(r"\bwriteFormat\s+ascii\s*;", control) or not re.search(r"\brunTimeModifiable\s+false\s*;", control):
        _fail("CASE_CODE_FORBIDDEN", "Only immutable ASCII cases are supported")
    return manifest, files


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)


class _Tokens:
    def __init__(self, text):
        self.tokens = iter(re.finditer(r'"(?:\\.|[^"\\])*"|[(){};]|[^\s(){};]+', text))
        self.current = None

    def take(self, expected=None):
        token = self.current
        if token is None:
            match = next(self.tokens, None)
            token = match.group() if match else None
        self.current = None
        if token is None or expected is not None and token != expected:
            _fail("MESH_FORMAT_INVALID", "Actual ASCII mesh syntax is incomplete or unsupported")
        return token

    def peek(self):
        if self.current is None:
            match = next(self.tokens, None)
            self.current = match.group() if match else None
        return self.current


def _mesh_tokens(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MESH_FILE_BYTES:
        _fail("MESH_FORMAT_INVALID", "Actual mesh file is missing, linked or oversized")
    try:
        text = _strip_comments(path.read_text(encoding="utf-8"))
    except UnicodeError:
        _fail("MESH_FORMAT_INVALID", "Binary/compressed mesh files are not supported by the bounded audit")
    tokens = _Tokens(text)
    tokens.take("FoamFile")
    header = _dictionary(tokens)
    if header.get("format") != ["ascii"]:
        _fail("MESH_FORMAT_INVALID", "Only actual ASCII mesh files are admitted")
    classes = {"points":{"vectorField"},"faces":{"faceList","faceCompactList"},"owner":{"labelList"},"neighbour":{"labelList"},
               "boundary":{"polyBoundaryMesh"},"cellZones":{"cellZoneList"}}
    if path.name in classes and (len(header.get("class",[])) != 1 or header["class"][0] not in classes[path.name]):
        _fail("MESH_FORMAT_INVALID", "Actual mesh file class differs from its supported native role")
    return tokens


def _dictionary(tokens):
    tokens.take("{")
    result = {}
    while tokens.peek() != "}":
        name = tokens.take()
        if name in result:
            _fail("MESH_FORMAT_INVALID", "Duplicate actual mesh dictionary entries")
        value, depth = [], 0
        while True:
            item = tokens.take()
            if item == ";" and depth == 0:
                break
            depth += item in ("(", "{")
            depth -= item in (")", "}")
            if depth < 0:
                _fail("MESH_FORMAT_INVALID", "Unterminated actual mesh entry")
            value.append(item)
            if len(value) > 40_000_000:
                _fail("MESH_FORMAT_INVALID", "Actual mesh entry exceeds its bound")
        result[name] = value
    tokens.take("}")
    return result


def _label(token, high):
    if not re.fullmatch(r"[0-9]{1,10}", token):
        _fail("MESH_FORMAT_INVALID", "Actual mesh labels must be nonnegative bounded integers")
    return _integer(int(token), "Mesh label", 0, high)


def _list(path, kind, max_count, max_label):
    tokens = _mesh_tokens(path)
    count = _label(tokens.take(), max_count)
    tokens.take("(")
    values = [] if kind != "labels" else array("i")
    for _ in range(count):
        if kind == "labels":
            values.append(_label(tokens.take(), max_label))
        elif kind == "points":
            tokens.take("(")
            try:
                vector = [_number(float(tokens.take()), "Mesh coordinate") for _ in range(3)]
            except ValueError:
                _fail("MESH_FORMAT_INVALID", "Actual mesh point is not numeric")
            tokens.take(")")
            values.append(vector)
        else:
            length = _label(tokens.take(), 128)
            if length < 3:
                _fail("MESH_TOPOLOGY_FAILED", "Actual mesh face has fewer than three points")
            tokens.take("(")
            face = [_label(tokens.take(), max_label) for _ in range(length)]
            tokens.take(")")
            if len(set(face)) != length:
                _fail("MESH_TOPOLOGY_FAILED", "Actual face repeats a point")
            values.append(face)
    tokens.take(")")
    if tokens.peek() is not None and tokens.take() != ";":
        _fail("MESH_FORMAT_INVALID", "Actual mesh list has trailing content")
    if tokens.peek() is not None:
        _fail("MESH_FORMAT_INVALID", "Actual mesh list has trailing content")
    return values


def _named_list(path, max_count):
    tokens = _mesh_tokens(path)
    count = _label(tokens.take(), max_count)
    tokens.take("(")
    result = {}
    for _ in range(count):
        name = tokens.take()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name in result:
            _fail("MESH_FORMAT_INVALID", "Actual patch/zone name is duplicate or unsupported")
        result[name] = _dictionary(tokens)
    tokens.take(")")
    if tokens.peek() is not None:
        _fail("MESH_FORMAT_INVALID", "Actual named mesh list has trailing content")
    return result


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def parse_check_mesh(text):
    """Require the real Foundation success conclusion; exit0 alone is inadequate."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_LOG_BYTES:
        _fail("MESH_CHECK_FAILED", "Native mesh-check log is missing or oversized")
    if re.search(r"Failed\s+\d+\s+mesh checks|FOAM FATAL|\b(?:nan|inf)\b", text, re.I) or not re.search(r"^\s*Mesh OK\.\s*$", text, re.M):
        _fail("MESH_CHECK_FAILED", "Native mesh check did not conclude Mesh OK")
    regions = re.findall(r"Number of regions:\s*([0-9]+)", text)
    if not regions or any(int(value) != 1 for value in regions):
        _fail("MESH_CONNECTIVITY_FAILED", "Native mesh check must report exactly one connected fluid region")
    cells = re.findall(r"^\s*cells:\s*([0-9]+)\s*$", text, re.M)
    result = {"mesh_ok": True, "connected_regions": 1, "reported_cell_count": int(cells[-1]) if cells else None}
    for name, pattern in {"max_nonorthogonality_deg": r"Mesh non-orthogonality Max:\s*([+\-0-9.eE]+)",
                          "max_skewness": r"Max skewness\s*=\s*([+\-0-9.eE]+)"}.items():
        found = re.findall(pattern, text)
        if found:
            result[name] = _number(float(found[-1]), "Actual mesh quality")
    return result


def audit_mesh(case_directory, manifest, native_report, check_log):
    """Independently check topology/zone arithmetic and bind native V/findCell.

    Source normalization uses mesh.V() read by the hash-bound native helper.
    Triangular-fan volumes are a separate geometric cross-check; their values
    never replace native finite-volume volumes on warped snappy faces.
    """
    base = Path(case_directory) / "constant" / "polyMesh"
    limit = manifest["config"]["max_cells"]
    check = parse_check_mesh(check_log)
    points = _list(base / "points", "points", limit * 24, limit * 24)
    faces = _list(base / "faces", "faces", limit * 24, max(0, len(points)-1))
    owners = _list(base / "owner", "labels", len(faces), limit-1)
    neighbours = _list(base / "neighbour", "labels", len(faces), limit-1)
    if not points or not faces or len(owners) != len(faces) or len(neighbours) >= len(faces):
        _fail("MESH_TOPOLOGY_FAILED", "Actual owner/neighbour addressing does not cover the fluid mesh")
    ncell = max(owners, default=-1)+1
    if ncell < 1 or check["reported_cell_count"] not in (None, ncell) or set(owners) | set(neighbours) != set(range(ncell)):
        _fail("MESH_TOPOLOGY_FAILED", "Actual fluid cell labels/counts are incomplete or inconsistent")
    parents = array("i", range(ncell))
    def find(value):
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value
    for owner, neighbour in zip(owners, neighbours):
        if owner >= neighbour:
            _fail("MESH_TOPOLOGY_FAILED", "Native internal faces must point from lower owner to higher neighbour")
        parents[find(neighbour)] = find(owner)
    if len({find(cell) for cell in range(ncell)}) != 1:
        _fail("MESH_CONNECTIVITY_FAILED", "Actual owner/neighbour graph contains disconnected fluid regions")
    native = native_report
    if not isinstance(native, dict) or native.get("schema_version") != "phase-b-native-mesh-audit-v1" or native.get("cell_count") != ncell:
        _fail("MESH_NATIVE_AUDIT_FAILED", "Native finite-volume audit count/schema differs from actual mesh")
    volumes = native.get("cell_volumes_m3")
    if not isinstance(volumes, list) or len(volumes) != ncell:
        _fail("MESH_NATIVE_AUDIT_FAILED", "Every actual native finite-volume cell volume is required")
    volumes = [_number(v, "Native finite-volume cell volume", positive=True) for v in volumes]
    geometric = array("d", [0.0])*ncell
    # Translate close to the domain to reduce cancellation for large coordinates.
    origin = points[0]
    for index, face in enumerate(faces):
        first = [a-b for a, b in zip(points[face[0]], origin)]
        volume = 0.0
        for i in range(1, len(face)-1):
            second = [a-b for a, b in zip(points[face[i]], origin)]
            third = [a-b for a, b in zip(points[face[i+1]], origin)]
            volume += _dot(first, _cross(second, third))/6
        geometric[owners[index]] += volume
        if index < len(neighbours):
            geometric[neighbours[index]] -= volume
    if any(not math.isfinite(v) or v <= 0 for v in geometric):
        _fail("MESH_VOLUME_FAILED", "Independent closed-face geometric volume is nonpositive")
    boundary = _named_list(base / "boundary", 1024)
    expected = {"inlet": "patch", "outlet": "patch", "ground": "wall", "top": "patch",
                "side_left": "symmetryPlane", "side_right": "symmetryPlane"}
    expected.update({item["patch"]: "wall" for item in manifest["geometry"]})
    if set(boundary) != set(expected):
        _fail("MESH_PATCH_FAILED", "Actual boundary patch identities differ from the frozen geometry")
    patches, start = [], len(neighbours)
    for name, data in boundary.items():
        if data.get("type") != [expected[name]] or len(data.get("nFaces", [])) != 1 or len(data.get("startFace", [])) != 1:
            _fail("MESH_PATCH_FAILED", "Actual boundary patch type/count metadata is incomplete")
        count = _label(data["nFaces"][0], len(faces))
        face_start = _label(data["startFace"][0], len(faces))
        if count < 1 or face_start != start or face_start + count > len(faces):
            _fail("MESH_PATCH_FAILED", "Every expected patch must contain contiguous nonempty actual faces")
        start += count
        patches.append({"name": name, "type": expected[name], "face_count": count, "start_face": face_start})
    if start != len(faces):
        _fail("MESH_PATCH_FAILED", "Actual boundary faces are not completely covered")
    zones = _named_list(base / "cellZones", 1000) if (base / "cellZones").exists() else {}
    expected_zones = {item["zone"]: item for item in manifest["sources"]}
    if set(zones) != set(expected_zones):
        _fail("SOURCE_ZONE_INVALID", "Actual source zones must cover frozen sources exactly")
    reports = native.get("zones", [])
    if not isinstance(reports, list) or len(reports) != len(zones) or {item.get("name") for item in reports} != set(zones):
        _fail("SOURCE_ZONE_INVALID", "Native source zones differ from actual mesh zone labels")
    native_zones = {item["name"]: item for item in reports}
    assignments = []
    for name, data in zones.items():
        tokens = data.get("cellLabels", [])
        if data.get("type") not in (None,["cellZone"]) or len(tokens) < 4 or tokens[0] not in ("List<label>", "labelList") or tokens[2] != "(" or tokens[-1] != ")":
            _fail("SOURCE_ZONE_INVALID", "Actual cell-zone addressing is unsupported")
        count = _label(tokens[1], ncell)
        cells = [_label(token, ncell-1) for token in tokens[3:-1]]
        if not cells or len(cells) != count or len(set(cells)) != count:
            _fail("SOURCE_ZONE_EMPTY", "Each source requires distinct nonempty actual fluid cells")
        volume = math.fsum(volumes[cell] for cell in cells)
        native_zone = native_zones[name]
        if native_zone.get("cell_count") != count or not math.isclose(_number(native_zone.get("volume_m3"), "Native zone volume", positive=True), volume, rel_tol=1e-10, abs_tol=1e-12):
            _fail("SOURCE_ZONE_INVALID", "Native source count/volume disagrees with actual label-addressed V")
        rate = _number(expected_zones[name]["rate_kg_s"], "Frozen source mass rate")
        if rate < 0:
            _fail("SOURCE_ZONE_INVALID", "Frozen source mass rate cannot be negative")
        density = rate/volume
        assigned = math.fsum(density*volumes[cell] for cell in cells)
        error = abs(assigned-rate)/rate if rate else abs(assigned)
        if error > 0.001:
            _fail("SOURCE_MASS_ASSIGNMENT_FAILED", "Actual native source allocation fails its 0.1% arithmetic gate")
        assignments.append({"zone": name, "fluid_cell_count": count, "fluid_volume_m3": volume,
                            "rate_kg_s": rate, "volume_specific_rate_kg_m3_s": density,
                            "assigned_rate_kg_s": assigned, "relative_assignment_error": error,
                            "provenance": "native_mesh_V_and_actual_cell_zone_labels"})
    receptors = native.get("receptors")
    if not isinstance(receptors, list) or len(receptors) != len(manifest["receptors"]):
        _fail("RECEPTOR_SUPPORT_FAILED", "Native mesh search must resolve every frozen receptor")
    supports = []
    for index, (receptor, item) in enumerate(zip(manifest["receptors"], receptors)):
        if not isinstance(item, dict) or item.get("index") != index:
            _fail("RECEPTOR_SUPPORT_FAILED", "Native receptor index differs from frozen ordering")
        cell = _integer(item.get("cell"), "Native receptor cell", 0, ncell-1)
        centre = item.get("cell_centre_m")
        if not isinstance(centre, list) or len(centre) != 3:
            _fail("RECEPTOR_SUPPORT_FAILED", "Native receptor cell centre is missing")
        centre = [_number(v, "Native receptor cell centre") for v in centre]
        distance = _number(item.get("distance_to_cell_centre_m"), "Native receptor centre distance")
        expected_distance = math.dist(centre, receptor["position_solver_m"])
        if distance < 0 or not math.isclose(distance, expected_distance, rel_tol=1e-10, abs_tol=1e-10):
            _fail("RECEPTOR_SUPPORT_FAILED", "Native receptor distance differs from its frozen location")
        supports.append({"id": receptor["id"], "cell": cell, "cell_centre_m": centre,
                         "distance_to_cell_centre_m": distance, "interpolation": "native_cell",
                         "native_search": "Foundation13_meshSearch_findCell_tetrahedral_default",
                         "representativeness_verified": False})
    hashes = {path.name: _file_hash(path) for path in sorted(base.iterdir()) if path.is_file()}
    return {"actual_execution_verified": True, "qualified": False, "cell_count": ncell,
            "fluid_volume_m3": math.fsum(volumes), "min_native_cell_volume_m3": min(volumes),
            "connected_regions": 1, "check_mesh": check, "patches": patches,
            "source_assignments": assignments, "receptors": supports, "mesh_file_hashes": hashes,
            "native_report_hash": _digest(native), "geometric_volume_crosscheck": {
                "method": "oriented_triangular_face_fan_divergence_theorem",
                "maximum_relative_cell_difference": max(abs(a-b)/b for a, b in zip(geometric, volumes)),
                "replaces_native_finite_volume_V": False}}


def _probe_series(path, field, receptors, iteration_limit):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MESH_FILE_BYTES:
        _fail("FIELD_EXTRACTION_FAILED", "Actual native probe series is missing, linked or oversized")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeError:
        _fail("FIELD_EXTRACTION_FAILED", "Actual native probe output is not ASCII text")
    locations, rows = {}, []
    for line in lines:
        if line.startswith("# Probe "):
            match = re.fullmatch(r"# Probe\s+([0-9]+)\s+\(([^()]*)\)\s*", line)
            if not match:
                _fail("FIELD_EXTRACTION_FAILED", "Actual native probe header is incomplete")
            index = int(match[1])
            if index in locations or index >= len(receptors):
                _fail("FIELD_EXTRACTION_FAILED", "Actual native probe header has duplicate/unknown support")
            try:
                point = [_number(float(v), "Native probe coordinate") for v in match[2].split()]
            except ValueError:
                _fail("FIELD_EXTRACTION_FAILED", "Actual native probe coordinates are unsupported")
            if len(point) != 3 or any(abs(a-b) > 5e-6*max(1,abs(b)) for a,b in zip(point,receptors[index]["position_solver_m"])):
                _fail("FIELD_EXTRACTION_FAILED", "Actual native probe coordinates differ from frozen support")
            locations[index] = point
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tokens = _Tokens(line)
        try:
            iteration = _number(float(tokens.take()), "Native steady iteration")
            if not iteration.is_integer() or not 0 <= iteration <= iteration_limit or rows and iteration <= rows[-1]["iteration"]:
                _fail("FIELD_EXTRACTION_FAILED", "Native probe iterations must be ordered, unique and bounded")
            values = []
            for _ in receptors:
                if field == "U":
                    tokens.take("(")
                    values.append([_number(float(tokens.take()), "Native velocity") for _ in range(3)])
                    tokens.take(")")
                else:
                    values.append(_number(float(tokens.take()), "Native scalar"))
            if tokens.peek() is not None:
                _fail("FIELD_EXTRACTION_FAILED", "Native probe row has unmatched values")
        except ValueError:
            _fail("FIELD_EXTRACTION_FAILED", "Native probe values are invalid or nonfinite")
        rows.append({"iteration": int(iteration), "values": values})
        if len(rows) > iteration_limit+1:
            _fail("FIELD_EXTRACTION_FAILED", "Native probe series exceeded its frozen iteration allowance")
    if set(locations) != set(range(len(receptors))) or not rows:
        _fail("FIELD_EXTRACTION_FAILED", "Native probe series must cover every frozen receptor")
    return rows


def analyse_fields(case_directory, manifest, mesh_report, native):
    """Actual native scalar/flux/support diagnostics, with every threshold stated.

    These limited numerical checks cannot establish empty-domain ABL, independent
    manufactured/benchmark verification, mesh sensitivity or observational
    qualification. A tested native run remains diagnostic even when these pass.
    """
    for name, expected in mesh_report.get("mesh_file_hashes", {}).items():
        path = Path(case_directory)/"constant/polyMesh"/name
        if not path.is_file() or _file_hash(path) != expected:
            _fail("FIELD_EXTRACTION_FAILED", "Static native mesh bytes changed between preparation and final fields")
    field = native.get("field_audit") if isinstance(native, dict) else None
    if not isinstance(field, dict) or field.get("scalar_dimensions") != [1,-3,0,0,0,0,0]:
        _fail("FIELD_EXTRACTION_FAILED", "Native scalar dimensions differ from kg/m3")
    cell_count = mesh_report["cell_count"]
    concentrations, volumes = field.get("cell_concentrations_kg_m3"), native.get("cell_volumes_m3")
    if not isinstance(concentrations, list) or not isinstance(volumes, list) or len(concentrations) != cell_count or len(volumes) != cell_count:
        _fail("FIELD_EXTRACTION_FAILED", "Every actual cell scalar and finite-volume volume is required")
    concentrations = [_number(v,"Native cell scalar") for v in concentrations]
    volumes = [_number(v,"Native cell volume",positive=True) for v in volumes]
    if not math.isclose(math.fsum(volumes),mesh_report["fluid_volume_m3"],rel_tol=1e-10,abs_tol=1e-12):
        _fail("FIELD_EXTRACTION_FAILED", "Latest fields do not use the frozen prepared fluid mesh")
    config = manifest["config"]
    background = _number(config["background_ug_m3"],"Frozen background")*1e-9
    rate = math.fsum(item["rate_kg_s"] for item in manifest["sources"])
    tolerance = max(1e-18,max(abs(background),max(abs(v) for v in concentrations))*1e-12)
    positive = min(concentrations) >= -tolerance
    boundary = field.get("boundary_fluxes")
    expected_patches = {item["name"] for item in mesh_report["patches"]}
    if not isinstance(boundary,list) or len(boundary) != len(expected_patches) or {item.get("patch") for item in boundary} != expected_patches:
        _fail("FIELD_EXTRACTION_FAILED", "Native transport fluxes must cover each actual boundary exactly")
    boundary_values = []
    for item in boundary:
        boundary_values.append({"patch":item["patch"],
                                **{key:_number(item.get(key),"Native outward boundary flux") for key in
                                   ("advective_outward_kg_s","diffusive_outward_kg_s","volume_outward_m3_s")}})
    net_mass = math.fsum(item["advective_outward_kg_s"]+item["diffusive_outward_kg_s"] for item in boundary_values)
    gross_mass = math.fsum(abs(item["advective_outward_kg_s"])+abs(item["diffusive_outward_kg_s"]) for item in boundary_values)/2
    mass_denominator = rate if rate > 0 else max(gross_mass,1e-18)
    mass_error = abs(net_mass-rate)/mass_denominator
    net_volume = math.fsum(item["volume_outward_m3_s"] for item in boundary_values)
    volume_scale = max(math.fsum(abs(item["volume_outward_m3_s"]) for item in boundary_values)/2,1e-18)
    volume_error = abs(net_volume)/volume_scale
    scalar = _probe_series(Path(case_directory)/"postProcessing/receptors/0/C","C",manifest["receptors"],config["flow_iterations"])
    velocity = _probe_series(Path(case_directory)/"postProcessing/receptors/0/U","U",manifest["receptors"],config["flow_iterations"])
    if [row["iteration"] for row in scalar] != [row["iteration"] for row in velocity]:
        _fail("FIELD_EXTRACTION_FAILED", "Velocity/scalar probe iterations must have identical actual support")
    iteration = _number(field.get("iteration"),"Native final steady iteration")
    if iteration != scalar[-1]["iteration"] or iteration != config["flow_iterations"]:
        _fail("FIELD_EXTRACTION_FAILED", "Final fields/probes do not reach the same frozen terminal iteration")
    native_receptors = field.get("receptor_fields")
    if not isinstance(native_receptors,list) or len(native_receptors) != len(manifest["receptors"]):
        _fail("FIELD_EXTRACTION_FAILED", "Actual final field audit must cover each native receptor")
    window = min(20,len(scalar))
    supports, stationarity = [], []
    for index,(receptor,support,item) in enumerate(zip(manifest["receptors"],mesh_report["receptors"],native_receptors)):
        if not isinstance(item,dict) or item.get("index") != index or item.get("cell") != support["cell"]:
            _fail("FIELD_EXTRACTION_FAILED", "Latest receptor fields changed native fluid cell support")
        c = _number(item.get("C_kg_m3"),"Actual native receptor scalar")
        u = item.get("U_m_s")
        if not isinstance(u,list) or len(u) != 3:
            _fail("FIELD_EXTRACTION_FAILED", "Actual native receptor velocity must contain three components")
        u = [_number(v,"Actual native receptor velocity") for v in u]
        if not math.isclose(c,scalar[-1]["values"][index],rel_tol=1e-5,abs_tol=1e-18) or any(not math.isclose(a,b,rel_tol=1e-5,abs_tol=1e-9) for a,b in zip(u,velocity[-1]["values"][index])):
            _fail("FIELD_EXTRACTION_FAILED", "Native latest-cell values disagree with the actual last probe row")
        history_c = [row["values"][index] for row in scalar[-window:]]
        history_u = [row["values"][index] for row in velocity[-window:]]
        increments = [v-background for v in history_c]
        c_scale = max(max(abs(v) for v in increments) if rate > 0 else max(abs(v) for v in history_c),1e-18)
        drift_c = (max(history_c)-min(history_c))/c_scale
        mean_u = [math.fsum(v[axis] for v in history_u)/window for axis in range(3)]
        drift_u = max(math.dist(v,mean_u) for v in history_u)/max(math.sqrt(_dot(mean_u,mean_u)),1e-12)
        stationarity.append({"id":receptor["id"],"samples":window,"window_start_iteration":scalar[-window]["iteration"],
                             "window_end_iteration":scalar[-1]["iteration"],"scalar_relative_range":drift_c,"velocity_relative_deviation":drift_u,
                             "scalar_denominator":"local_increment_if_emitting_else_total_scalar_with_1e-18_kg_m3_floor",
                             "target":.01,"passed":window >= 3 and drift_c <= .01 and drift_u <= .01})
        supports.append({"id":receptor["id"],"cell":support["cell"],"C_kg_m3":c,"C_ug_m3":c*1e9,
                         "background_ug_m3":background*1e9,"local_increment_ug_m3":(c-background)*1e9,
                         "U_m_s":u,"speed_m_s":math.sqrt(_dot(u,u)),"protected":receptor.get("protected",False)})
    gates = {"native_scalar_positivity":positive,"outward_boundary_mass_conservation":mass_error < .01,
             "boundary_volume_continuity":volume_error < .01,"native_receptor_probe_support":True,
             "receptor_scalar_velocity_stationarity":all(item["passed"] for item in stationarity)}
    if rate == 0:
        uniform_error = max(abs(v-background) for v in concentrations)/max(abs(background),1e-18)
        gates["no_source_uniform_background_preservation"] = uniform_error <= 1e-6
    else:
        uniform_error = None
    return {"schema_version":"phase-b-native-field-diagnostics-v1","qualified":False,"numerical_verification_passed":False,
            "limited_checks_passed":all(gates.values()),"gates":gates,"terminal_iteration":iteration,
            "temporal_support":"stationary_solver_iteration_end_state_not_measured_interval_mean",
            "C_unit":"kg/m3","reported_C_unit":"ug/m3","ug_per_kg":1e9,
            "scalar":{"minimum_kg_m3":min(concentrations),"maximum_kg_m3":max(concentrations),
                      "positivity_tolerance_kg_m3":tolerance,"negative_cell_count":sum(v < 0 for v in concentrations),
                      "volume_weighted_mean_kg_m3":math.fsum(c*v for c,v in zip(concentrations,volumes))/math.fsum(volumes),
                      "inventory_mass_kg":math.fsum(c*v for c,v in zip(concentrations,volumes)),
                      "local_increment_inventory_kg":math.fsum((c-background)*v for c,v in zip(concentrations,volumes)),
                      "no_source_uniform_relative_error":uniform_error},
            "mass_balance":{"specified_source_kg_s":rate,"net_outward_kg_s":net_mass,"absolute_residual_kg_s":net_mass-rate,
                            "normalisation_kg_s":mass_denominator,"relative_imbalance":mass_error,"target":.01,
                            "normalisation":"source_rate_when_positive_else_half_gross_boundary_mass_with_1e-18_kg_s_floor",
                            "advection":"actual_phi_times_boundary_C_for_controlled_Gauss_upwind",
                            "diffusion":"minus_linear_interpolated_D_times_corrected_snGrad_C_times_boundary_area",
                            "D":"specified_molecular_plus_native_nut_over_turbulent_Schmidt"},
            "volume_continuity":{"net_outward_m3_s":net_volume,"relative_imbalance":volume_error,"target":.01},
            "boundary_fluxes":boundary_values,"receptors":supports,"stationarity":stationarity,
            "probe_series":{"C":scalar,"U":velocity,"iteration_is_physical_seconds":False},
            "native_audit_hash":_digest(native),"unexecuted_required_gates":["empty_domain_ABL_and_wall_support", "manufactured_transport",
                "independent_flow_and_tracer_benchmarks","three_mesh_domain_closure_sensitivity","whole_field_stationarity",
                "held_out_measurements_and_uncertainty","independent_scientific_review"]}


def _tree_usage(root, *, hashes=False):
    total, count, result = 0, 0, {}
    for path in Path(root).rglob("*"):
        if path.is_symlink():
            _fail("EXECUTION_OUTPUT_INVALID", "Native output unexpectedly contains a filesystem link")
        if path.is_file():
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                # Native writes/purgeWrite rename or remove time files between
                # directory enumeration and stat. Only the live size monitor
                # tolerates this exact disappearance; final hashing is strict.
                if hashes:
                    raise
                continue
            count += 1
            total += size
            if count > 10000 or total > MAX_OUTPUT_BYTES:
                _fail("COMPUTE_LIMIT_EXCEEDED", "Native output exceeded its bounded file/byte allowance")
            if hashes:
                result[path.relative_to(root).as_posix()] = _file_hash(path)
    return total, result


def _proc_resources(group):
    rss, cpu = 0, 0.0
    ticks = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text()
            fields = text[text.rfind(")")+2:].split()
            if int(fields[2]) == group:
                cpu += (int(fields[11])+int(fields[12]))/ticks
                rss += int(fields[21])*page
        except (OSError, ValueError, IndexError):
            continue
    return rss, cpu


def _stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _resource_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_AS, (MAX_RSS_BYTES, MAX_RSS_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def _run_application(runtime, argv, root, log_name, deadline, cancel_check, previous_log_bytes):
    if not argv or argv[0] not in COMMANDS:
        _fail("EXECUTION_COMMAND_FORBIDDEN", "Only fixed native pipeline applications may run")
    path = runtime.commands[argv[0]]
    if _file_hash(path) != runtime.files[path]:
        _fail("RUNTIME_INTEGRITY_ERROR", "Native application changed after runtime verification")
    destination = root / "logs" / (log_name + ".log")
    started = time.monotonic()
    peak, cpu, failure = 0, 0.0, None
    with destination.open("xb") as stream:
        process = subprocess.Popen([path, *argv[1:]], cwd=root, env=runtime.environment, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, shell=False, start_new_session=True,
                                   preexec_fn=_resource_limits)
        try:
            while process.poll() is None:
                if cancel_check and cancel_check():
                    _fail("RUN_CANCELLED", "Detailed execution was cancelled before publication")
                if time.monotonic() >= deadline:
                    _fail("COMPUTE_LIMIT_EXCEEDED", "Detailed execution reached its total wall-time ceiling")
                size = destination.stat().st_size
                if previous_log_bytes + size > MAX_LOG_BYTES:
                    _fail("COMPUTE_LIMIT_EXCEEDED", "Detailed execution reached its cumulative log-byte ceiling")
                _tree_usage(root)
                rss, measured_cpu = _proc_resources(process.pid)
                peak, cpu = max(peak, rss), max(cpu, measured_cpu)
                if rss > MAX_RSS_BYTES:
                    _fail("COMPUTE_LIMIT_EXCEEDED", "Detailed execution reached its sampled process-group memory ceiling")
                time.sleep(0.1)
        except BaseException as exc:
            failure = exc
            _stop_group(process)
        finally:
            # Terminate any leftover descendants even if the main child exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    size = destination.stat().st_size
    raw = destination.read_bytes()[:MAX_LOG_BYTES+1]
    log = raw.decode("utf-8", errors="replace")
    metrics = {"application": argv[0], "arguments": list(argv[1:]), "exit_code": process.returncode,
               "wall_seconds": time.monotonic()-started, "peak_sampled_rss_bytes": peak,
               "sampled_cpu_seconds": cpu, "log_bytes": size, "raw_log_hash": _file_hash(destination)}
    if failure:
        if isinstance(failure, ExecutionError):
            failure.details.update(command=metrics)
            raise failure
        if isinstance(failure, OSError):
            _fail("EXECUTION_IO_ERROR", "Native output monitoring failed; the process group was terminated",
                  command=metrics, error_type=type(failure).__name__, errno=failure.errno)
        raise failure
    if previous_log_bytes + size > MAX_LOG_BYTES:
        _fail("COMPUTE_LIMIT_EXCEEDED", "Native log bytes exceeded their cumulative allowance", command=metrics)
    if process.returncode != 0 or re.search(r"FOAM FATAL|Floating point exception|Segmentation fault", log):
        _fail("NATIVE_APPLICATION_FAILED", "Native pipeline application failed; diagnostics are retained", command=metrics)
    _tree_usage(root)
    return log, metrics


def _redacted_logs(root, runtime):
    result = {}
    replacements = {str(root): "[case]", str(runtime.root): "[Foundation13]"}
    home = runtime.environment.get("HOME")
    if home:
        replacements[home] = "[worker-home]"
    for path in sorted((root / "logs").glob("*.log")):
        text = path.read_bytes()[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
        for private, label in sorted(replacements.items(), key=lambda item: -len(item[0])):
            text = text.replace(private, label)
        result[path.name] = text
    return result


def execute_case(bundle, *, runtime_receipt, workspace_root, execution_id, fence=0, cancel_check=None, solve=True):
    """Run a checked immutable case with fixed applications and retain failures.

    A successfully solved case is ``diagnostic_only`` until a separate numerical
    qualification pipeline has actually passed. ``complete_unqualified`` is not
    emitted here. A failed check is ``failed``, never a successful comparison.
    """
    if not isinstance(execution_id, str) or not SAFE_ID.fullmatch(execution_id):
        _fail("EXECUTION_INPUT_INVALID", "Execution identity must be a controlled safe identifier")
    _integer(fence, "Attempt fence", 0, 2**31-1)
    if not isinstance(solve, bool):
        _fail("EXECUTION_INPUT_INVALID", "Solve selection must be boolean")
    manifest, files = validate_bundle(bundle)
    runtime = verify_runtime(runtime_receipt)
    root = _real_directory(workspace_root) / f"{execution_id}-f{fence}"
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        _fail("RUN_CONFLICT", "Immutable native attempt directory already exists; use a new attempt fence")
    for name, text in sorted(files.items()):
        destination = root / name
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
        if _file_hash(destination) != manifest["file_hashes"][name]:
            _fail("CASE_INTEGRITY_ERROR", "Materialized native case differs from its frozen input")
    (root / "logs").mkdir(mode=0o700)
    receptor_points = manifest.get("receptors", [])
    vectors = []
    for item in receptor_points:
        point = item.get("position_solver_m")
        if not isinstance(point, list) or len(point) != 3:
            _fail("EXECUTION_INPUT_INVALID", "Frozen receptor points must contain three coordinates")
        vectors.append("(" + " ".join(format(_number(v, "Frozen receptor coordinate"), ".17g") for v in point) + ")")
    helper_input = 'FoamFile { format ascii; class dictionary; object uaMeshAuditDict; }\nreceptorPoints\n(\n' + "\n".join(vectors) + '\n);\n'
    helper_input += "molecularDiffusivity " + format(_number(manifest["config"]["molecular_diffusivity_m2_s"],"Frozen molecular diffusivity"),".17g") + ";\n"
    helper_input += "turbulentSchmidt " + format(_number(manifest["config"]["turbulent_schmidt_number"],"Frozen turbulent Schmidt",positive=True),".17g") + ";\n"
    (root / "system" / "uaMeshAuditDict").write_text(helper_input, encoding="utf-8", newline="\n")
    (root / "frozen-bundle.json").write_bytes(_canonical(bundle))
    started, start_utc = time.monotonic(), datetime.now(timezone.utc).isoformat()
    deadline = started + manifest["config"]["max_wall_seconds"]
    receipt = {"schema_version": SCHEMA, "execution_id": execution_id, "fence": fence,
               "status": "failed", "qualified": False, "solver_executed": False,
               "numerical_verification_passed": False, "results_eligible_for_comparison": False,
               "started_at": start_utc, "mesh": None, "commands": [],
               "diagnostics": {"numerical_verification_passed": False, "issues": []}, "logs": {},
               "attestation": {"runtime_digest": runtime.digest, "runtime_identity": runtime.provenance["runtime_identity"],
                               "execution_id": execution_id, "fence": fence, "case_bundle_checksum": _digest(bundle),
                               "executor_hash": EXECUTOR_HASH, "bundle_hash": _digest(bundle), "manifest_hash": _digest(manifest),
                               "scenario_hash": manifest["scenario_hash"], "config_hash": manifest["config_hash"],
                               "prepared_file_hashes": dict(manifest["file_hashes"]), "helper_input_hash": _file_hash(root / "system" / "uaMeshAuditDict"),
                               "shell": False, "uploaded_commands": False},
               "limitations": ["Native execution is diagnostic, scientifically unqualified.",
                               "Steady iteration indices are not physical observation averaging times.",
                               "Mesh/source/receptor checks do not establish ABL, stationarity, conservation, benchmark accuracy or uncertainty.",
                               "OS/network isolation requires the worker deployment; this process wrapper is not a sandbox."]}
    cumulative_log = 0
    def run(argv, label):
        nonlocal cumulative_log
        log, metrics = _run_application(runtime, argv, root, label, deadline, cancel_check, cumulative_log)
        cumulative_log += metrics["log_bytes"]
        receipt["commands"].append(metrics)
        return log
    try:
        run(["blockMesh"], "01-blockMesh")
        if manifest["geometry"] or manifest["config"].get("surface_refinement_level", 0):
            run(["snappyHexMesh", "-overwrite"], "02-snappyHexMesh")
        check_log = run(["checkMesh", "-allTopology", "-allGeometry", "-meshQuality"], "03-checkMesh")
        parse_check_mesh(check_log)
        run(["topoSet"], "04-topoSet")
        run(["uaMeshAudit"], "05-uaMeshAudit")
        native = _json_file(root / "uaMeshAudit.json", MAX_MESH_FILE_BYTES)
        receipt["mesh"] = audit_mesh(root, manifest, native, check_log)
        receipt["attestation"]["native_audit_file_hash"] = _file_hash(root / "uaMeshAudit.json")
        run(["setAtmBoundaryLayer", "-time", "0"], "06-setAtmBoundaryLayer")
        if solve:
            receipt["solver_executed"] = True
            run(["foamRun", "-solver", "incompressibleFluid"], "07-foamRun")
            run(["uaMeshAudit", "-fields", "-latestTime"], "08-uaFieldAudit")
            field_report = _json_file(root / "uaFieldAudit.json", MAX_OUTPUT_BYTES//2)
            receipt["analysis"] = analyse_fields(root, manifest, receipt["mesh"], field_report)
            receipt["attestation"]["native_field_audit_file_hash"] = _file_hash(root / "uaFieldAudit.json")
            receipt["diagnostics"]["numerical_gates"] = receipt["analysis"]["gates"]
            if not receipt["analysis"]["limited_checks_passed"]:
                _fail("NUMERICAL_DIAGNOSTIC_FAILED", "Actual scalar/flux/stationarity checks failed; retain research diagnostics")
        if cancel_check and cancel_check():
            _fail("RUN_CANCELLED", "Detailed execution was cancelled before publication")
        if any(_file_hash(path) != expected for path, expected in runtime.files.items()):
            _fail("RUNTIME_INTEGRITY_ERROR", "Native runtime bytes changed during the attempt")
        receipt["status"] = "executed_unqualified"
        receipt["diagnostics"]["issues"].append({"code": "NUMERICAL_QUALIFICATION_UNEXECUTED", "message": "Native commands completed; separate numerical and scientific admission remains required"})
    except (ExecutionError, OSError, MemoryError, ValueError, KeyError) as exc:
        if isinstance(exc, ExecutionError):
            if exc.details.get("command"):
                receipt["commands"].append(exc.details["command"])
            if exc.code == "RUN_CANCELLED":
                receipt["status"] = "cancelled"
            issue = {"code": exc.code, "message": exc.message}
            issue.update({key:exc.details[key] for key in ("error_type","errno") if key in exc.details})
            receipt["diagnostics"]["issues"].append(issue)
        else:
            receipt["diagnostics"]["issues"].append({"code": "NATIVE_EXECUTION_FAILED", "message": "Native attempt failed; bounded logs and completed stages are retained"})
    receipt["logs"] = _redacted_logs(root, runtime)
    receipt["resources"] = {"wall_seconds": time.monotonic()-started,
                            "peak_sampled_rss_bytes": max((c.get("peak_sampled_rss_bytes", 0) for c in receipt["commands"]), default=0),
                            "sampled_cpu_seconds": sum(c.get("sampled_cpu_seconds", 0) for c in receipt["commands"]),
                            "limits": {"wall_seconds": manifest["config"]["max_wall_seconds"], "log_bytes": MAX_LOG_BYTES,
                                       "output_bytes": MAX_OUTPUT_BYTES, "resident_memory_bytes": MAX_RSS_BYTES,
                                       "max_cells": manifest["config"]["max_cells"], "threads": 2},
                            "memory_measurement": "sampled_proc_process_group_RSS_not_cgroup_peak", "cpu_measurement": "sampled_not_accounting_bill"}
    # Bounded output failures still retain hashes of readable files, without
    # re-opening an unbounded tree after the limit exception.
    try:
        output_bytes, hashes = _tree_usage(root, hashes=True)
        receipt["resources"]["output_bytes"] = output_bytes
        receipt["attestation"]["output_file_hashes"] = hashes
    except ExecutionError:
        receipt["status"] = "failed"
        receipt["diagnostics"]["issues"].append({"code": "COMPUTE_LIMIT_EXCEEDED", "message": "Output tree exceeds its publication allowance"})
        receipt["attestation"]["output_file_hashes"] = {}
    receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
    receipt["attestation"]["receipt_content_hash"] = _digest(receipt)
    (root / "execution-receipt.json").write_bytes(_canonical(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Urban Air Studio fixed native Foundation13 worker")
    sub = parser.add_subparsers(dest="operation", required=True)
    ready = sub.add_parser("readiness")
    ready.add_argument("--runtime-receipt", required=True)
    execute = sub.add_parser("execute")
    for argument in ("input", "output", "runtime-receipt", "workspace-root", "execution-id"):
        execute.add_argument("--"+argument, required=True)
    execute.add_argument("--fence", type=int, required=True)
    execute.add_argument("--cancel-file", required=True)
    execute.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.operation == "readiness":
        print(json.dumps(runtime_readiness(receipt_path=args.runtime_receipt), allow_nan=False))
        return 0
    try:
        receipt = execute_case(_json_file(args.input, MAX_INPUT_BYTES*2),
                               runtime_receipt=_json_file(args.runtime_receipt, 4*1024*1024),
                               workspace_root=args.workspace_root, execution_id=args.execution_id,
                               fence=args.fence, solve=not args.prepare_only,
                               cancel_check=lambda: Path(args.cancel_file).exists())
    except (ExecutionError, OSError, ValueError, KeyError, TypeError) as exc:
        receipt = {"schema_version": SCHEMA, "status": "failed", "qualified": False,
                   "numerical_verification_passed": False,
                   "diagnostics": {"numerical_verification_passed": False,
                                   "issues": [{"code": exc.code if isinstance(exc, ExecutionError) else "NATIVE_EXECUTION_FAILED",
                                    "message": exc.message if isinstance(exc, ExecutionError) else "Native worker preflight failed"}]}}
    destination = Path(args.output)
    if destination.is_symlink() or destination.exists():
        print(json.dumps({"status": "failed", "error_code": "EXECUTION_OUTPUT_CONFLICT"}))
        return 2
    with destination.open("xb") as stream:
        stream.write(_canonical(receipt))
    print(json.dumps({"status": receipt["status"], "qualified": False, "schema_version": SCHEMA}))
    return 0 if receipt["status"] == "executed_unqualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
