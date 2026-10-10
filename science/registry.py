import json
from pathlib import Path


def parameter_registry() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "datasets" / "parameter_registry.json").read_text(encoding="utf-8"))


def source_rights_matrix() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "datasets" / "source_rights_matrix.json").read_text(encoding="utf-8"))
