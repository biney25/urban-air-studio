"""Bounded cached platform checks, separate from public process liveness."""
from contextlib import closing
import json
import threading
import time

from api.auth import public_config


def check_platform(request, store_factory):
    config = public_config()
    if not config["configured"]:
        return 503, {"status":"not_ready", "code":"IDENTITY_NOT_CONFIGURED"}
    try:
        store = store_factory(request)
        with closing(store.connect()) as db:
            if db.execute("SELECT 1").fetchone()[0] != 1:
                raise ValueError("Database probe failed")
            if config["mode"] == "cognito":
                from api.postgres import SCHEMA_VERSION
                if not db.execute("SELECT version FROM urban.schema_versions WHERE version=?",(SCHEMA_VERSION,)).fetchone():
                    raise ValueError("Migration absent")
                db.execute("SELECT PostGIS_Version()").fetchone()
    except Exception:
        # No DSNs, passwords, cloud ARNs or internal exception strings escape.
        return 503, {"status":"not_ready", "code":"DATABASE_NOT_READY"}
    if config["mode"] == "cognito":
        try:
            assets = store.artifacts
            response = assets.client.get_object(Bucket=assets.bucket, Key=assets.prefix+"/platform-readiness.json")
            stream = response["Body"]
            try:
                length = response.get("ContentLength")
                if isinstance(length,bool) or not isinstance(length,int) or not 1 <= length <= 4096:
                    raise ValueError("Invalid readiness marker size")
                payload = stream.read(length+1)
                if len(payload) != length or response.get("ServerSideEncryption") not in {"AES256","aws:kms"}:
                    raise ValueError("Incomplete or unencrypted readiness marker")
                if json.loads(payload).get("schema_version") != "platform-readiness-v1":
                    raise ValueError("Invalid readiness marker")
            finally:
                stream.close()
        except Exception:
            return 503, {"status":"not_ready", "code":"ASSET_READ_NOT_READY"}
    return 200, {"status":"ready", "mode":config["mode"], "adapter":store.adapter_name,
                 "scientific_qualification":"research_preview", "cloud_operation_accepted":False}


class Readiness:
    """Serialize concurrent probes; reuse a success/failure for ten seconds.

    This establishes DB/schema and one scoped encrypted object read, not S3 write
    capability, worker freshness, provider login or full operational acceptance.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0.0

    def probe(self, request, store_factory):
        if not public_config()["configured"]:
            return 503, {"status":"not_ready", "code":"IDENTITY_NOT_CONFIGURED"}
        with self.lock:
            if self.cached is None or time.monotonic() >= self.expires:
                self.cached = check_platform(request,store_factory)
                self.expires = time.monotonic()+10
            return self.cached
