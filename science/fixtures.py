from __future__ import annotations

import json
from pathlib import Path


def load_fixture() -> dict:
    """A fictional, redistributable integration fixture; it is not a Delhi survey."""
    path = Path(__file__).resolve().parent.parent / "datasets" / "fictional_campus.geojson"
    return json.loads(path.read_text(encoding="utf-8"))
