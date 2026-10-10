"""Real local API + native Cedar demo in an isolated illustrative store.

Uses FastAPI's in-process ASGI test transport, not a deployed network endpoint.
Never edits the live Studio database, policy or existing source records.
"""
from pathlib import Path
import sys
ROOT = next(p for p in Path(__file__).resolve().parents if (p / "api/authorization.py").is_file())
sys.path.insert(0, str(ROOT))
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile

os.environ["UA_MODE"] = "local"
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
from fastapi.testclient import TestClient
from api.auth import Identity
from api.main import create_app, local_identity
from api.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="New local JSON evidence path; never overwrite")
    args = parser.parse_args()
    destination = (ROOT / args.output).resolve() if args.output else None
    if destination and (not destination.is_relative_to(ROOT) or destination.exists()):
        raise ValueError("Choose a new evidence file within this project")
    scratch = ROOT / ".runtime/cedar-demo"
    scratch.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="isolated-", dir=scratch) as directory:
        app = create_app()
        app.state.store = Store(Path(directory))
        app.dependency_overrides[local_identity] = lambda: Identity("demo-tenant", "demo-planner")
        with TestClient(app) as client:
            p = client.post("/api/projects", json={"name": "Illustrative Cedar demonstration"})
            assert p.status_code == 201
            prefix = f"/api/projects/{p.json()['id']}"
            method = client.get(prefix + "/qualification").json()["authorization_method"]
            assert method["available"] and method["engine"] == "cedar-policy" and method["cloud_service"] is False
            template = json.loads((ROOT / "datasets/phase_b/inventory-illustrative.json").read_text())

            def proof(reply, expected, boundary):
                assert reply.status_code == expected
                assert reply.headers["x-authorization-engine"] == "cedar-policy/4.12.0"
                assert reply.headers["x-authorization-policy-sha256"] == method["policy_sha256"]
                assert reply.headers["x-authorization-decision-id"].startswith("authz_")
                if expected == 403:
                    detail = reply.json()["detail"]
                    assert detail["code"] == "LICENCE_USE_BLOCKED" and detail["authorization"]["decision"] == "Deny"
                rows.append({"operation": boundary, "http_status": reply.status_code,
                             "decision": "Deny" if expected == 403 else "Allow",
                             "engine": reply.headers["x-authorization-engine"],
                             "policy_sha256": reply.headers["x-authorization-policy-sha256"]})

            for allowed in (True, False):
                body = deepcopy(template)
                body["title"] = "Illustrative export " + ("permitted" if allowed else "restricted")
                body["rights"]["export"] = allowed
                created = client.post(prefix + "/evidence", json=body)
                proof(created, 201, "derive illustrative inventory")
                exported = client.get(prefix + "/evidence/" + created.json()["id"] + "/download")
                proof(exported, 200 if allowed else 403, "export " + ("permitted" if allowed else "restricted") + " inventory")
                if allowed:
                    assert exported.headers["cache-control"] == "no-store" and exported.content
    result = {"schema_version": "hackathon-cedar-demonstration-v1", "success": True,
              "recorded_at": datetime.now(timezone.utc).isoformat(), "qualified": False,
              "transport": "actual in-process ASGI API; real native Cedar; isolated illustrative store; no mocks",
              "live_project_database_modified": False, "aws_deployed": False, "cloud_spend_usd": 0,
              "checks": rows,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ("api/authorization.py", "api/policies/phase_b.cedar", "api/policies/phase_b.cedarschema")}}
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as out:
            json.dump(result, out, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
