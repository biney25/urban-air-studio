"""Local Cedar source-permission gate. Identity/scope/integrity stay in the API."""
from importlib import import_module, metadata
import hashlib
import json
from pathlib import Path
import platform
import sys
import uuid
from typing import Literal

from fastapi import HTTPException

from api.responses import Output
from api.schemas import Rights

POLICY_DIRECTORY=Path(__file__).resolve().parent/"policies"
POLICY_ID="phase-b-same-project-source-permission"
POLICY_SHA256="1e9ee0ba52a4668ab9c0a7a3840e99608da389122d1e89203991f13f6f6446f3"
SCHEMA_SHA256="0d1255094f94ef28e3898b4ac4fb40ee6bd6f4e4fd9b417eb0e98871d1267880"
PROVENANCE_SHA256="c5c2fd5dfe9a9c4839aa86f24baaba6ad64d4acd98d21aec69ff616f5def1aec"
VERSION="4.12.0"
ACTIONS={"derive","solve","export"}
WRAPPER_HASHES={"__init__.py":"1624e2e970347b21d4e1d8ab7c5066660f5d92f5ee5158fde5fd9eb754e568c8",
                "pst.py":"5ad53c005289d55fa32030065cb34c4dcf631dbe0cb47cf340ace08c01cca687"}


class AuthorizationMethod(Output):
    id: str
    state: Literal["active","unavailable_fail_closed"]
    available: bool
    engine: str
    engine_version: str
    binding: str
    binding_version: str
    binding_maintainer: str
    binding_support: str
    license: str
    aws_origin: str
    policy_id: str
    policy_sha256: str
    schema_sha256: str
    provenance_sha256: str
    actions: list[str]
    fail_closed: bool
    error_diagnostics_blocked: bool
    local_evaluation: bool
    cloud_service: bool
    scope: str
    source_urls: dict[str,str]


def _bytes(path, maximum=2_000_000):
    if path.stat().st_size>maximum:
        raise ValueError("Authorization input exceeds its fixed bound")
    content=path.read_bytes()
    if len(content)>maximum:
        raise ValueError("Authorization input grew beyond its fixed bound")
    return content


def _checked(path, sha256):
    content=_bytes(path)
    if hashlib.sha256(content).hexdigest()!=sha256:
        raise ValueError("Authorization contract identity changed")
    return content


def _load_engine(provenance):
    """Verify the tested wheel's runtime identity without any network call."""
    distribution=metadata.distribution("cedarpy")
    if distribution.version!=VERSION:
        raise ValueError("Untested Cedar binding version")
    if (sys.implementation.name!="cpython" or sys.version_info[:2]!=(3,13)
            or platform.machine().lower() not in {"amd64","x86_64"} or sys.platform not in provenance["wheels"]):
        raise ValueError("Untested Cedar platform")
    package=Path(distribution.locate_file("cedarpy")).resolve()
    natives=[Path(distribution.locate_file(file)).resolve() for file in distribution.files
             if str(file).startswith("cedarpy/_internal.") and str(file).endswith((".pyd",".so"))]
    if len(natives)!=1 or not natives[0].is_relative_to(package):
        raise ValueError("Cedar native library metadata is invalid")
    native=_bytes(natives[0],64_000_000)
    if hashlib.sha256(native).hexdigest()!=provenance["wheels"][sys.platform]["native_sha256"]:
        raise ValueError("Cedar native library differs from its verified wheel")
    for filename,expected in WRAPPER_HASHES.items():
        content=_bytes(package/filename).replace(b"\r\n",b"\n")
        if hashlib.sha256(content).hexdigest()!=expected:
            raise ValueError("Cedar wrapper differs from its verified wheel")
    sbom_path=distribution.locate_file(f"cedarpy-{VERSION}.dist-info/sboms/cedarpy.cyclonedx.json")
    sbom=json.loads(_bytes(Path(sbom_path)))
    components={row["name"]:row["version"] for row in sbom["components"] if row["name"].startswith("cedar-policy")}
    if components!={"cedar-policy":VERSION,"cedar-policy-core":VERSION,"cedar-policy-formatter":VERSION}:
        raise ValueError("Cedar core SBOM differs from pinned provenance")
    engine=import_module("cedarpy")
    if Path(engine.__file__).resolve()!=package/"__init__.py" or Path(engine._internal.__file__).resolve()!=natives[0]:
        raise ValueError("Loaded Cedar runtime does not match verified installed files")
    return engine


class Authorizer:
    def __init__(self, policy_directory=POLICY_DIRECTORY):
        self.directory=Path(policy_directory)
        self.available=False
        self.engine=None
        self.policy_set=None
        self.schema=None
        try:
            policy,schema,provenance=self._contracts()
            self.engine=_load_engine(provenance)
            self.schema=self.engine.Schema.from_str(schema)
            validation=self.engine.validate_policies(policy,self.schema)
            if not validation.validation_passed or validation.errors:
                raise ValueError("Cedar policy failed schema validation")
            self.policy_set=self.engine.PolicySet.from_str(policy)
            self.available=True
        except Exception:
            # No exception text, private paths or entity payload is exposed.
            # Phase A may remain available; every protected Phase B action fails.
            self.available=False

    def _contracts(self):
        policy=_checked(self.directory/"phase_b.cedar",POLICY_SHA256).decode("utf-8")
        schema=_checked(self.directory/"phase_b.cedarschema",SCHEMA_SHA256).decode("utf-8")
        provenance=json.loads(_checked(self.directory/"cedar-provenance.json",PROVENANCE_SHA256))
        return policy,schema,provenance

    def status(self):
        if self.available:
            try:
                self._contracts()
            except Exception:
                self.available=False
        return {"id":"cedar-phase-b-permissions-v1","state":"active" if self.available else "unavailable_fail_closed",
            "available":self.available,"engine":"cedar-policy","engine_version":VERSION,"binding":"cedarpy","binding_version":VERSION,
            "binding_maintainer":"k9securityio","binding_support":"Community binding; not officially supported by AWS or the Cedar Policy team",
            "license":"Apache-2.0","aws_origin":"AWS-origin open-source Cedar policy engine","policy_id":POLICY_ID,
            "policy_sha256":"sha256:"+POLICY_SHA256,"schema_sha256":"sha256:"+SCHEMA_SHA256,
            "provenance_sha256":"sha256:"+PROVENANCE_SHA256,"actions":["derive","solve","export"],
            "fail_closed":True,"error_diagnostics_blocked":True,"local_evaluation":True,"cloud_service":False,
            "scope":"Phase B source permissions only; identity, project ownership and immutable input checks remain separate.",
            "source_urls":{"engine":"https://github.com/cedar-policy/cedar","binding":"https://github.com/k9securityio/cedar-py",
                           "aws_origin":"https://aws.amazon.com/tw/about-aws/whats-new/2023/05/cedar-open-source-language-access-control/"}}

    def check(self):
        return PermissionCheck(self)

    def evaluate(self, identity, project_id, *, action, identifier, tenant_id, resource_project_id, rights):
        if not self.status()["available"]:
            return "unavailable"
        if action not in ACTIONS:
            return "denied"
        try:
            identifiers=(identity.tenant_id,identity.user_id,project_id,identifier,tenant_id,resource_project_id)
            if any(not isinstance(value,str) or not 1<=len(value)<=200 or "\0" in value for value in identifiers):
                raise ValueError("Invalid trusted identity/resource support")
            permissions=Rights.model_validate(rights).model_dump()
            entities=[{"uid":{"type":"UrbanAir::User","id":identity.tenant_id+":"+identity.user_id},
                       "attrs":{"tenant":identity.tenant_id,"projects":[project_id]},"parents":[]},
                      {"uid":{"type":"UrbanAir::Asset","id":identifier},
                       "attrs":{"tenant":tenant_id,"project":resource_project_id,"rights":permissions},"parents":[]}]
            request={"principal":{"type":"UrbanAir::User","id":identity.tenant_id+":"+identity.user_id},
                     "action":{"type":"UrbanAir::Action","id":action},
                     "resource":{"type":"UrbanAir::Asset","id":identifier},"context":{}}
            result=self.engine.is_authorized(request,self.policy_set,entities,schema=self.schema)
            if result.diagnostics.errors:
                return "error"
            return "allowed" if result.decision==self.engine.Decision.Allow else "denied"
        except Exception:
            return "error"


class PermissionCheck:
    """One compound request ID; every required decision must allow cleanly."""
    def __init__(self, authorizer):
        self.authorizer=authorizer
        self.decision_id="authz_"+uuid.uuid4().hex

    def headers(self):
        return {"X-Authorization-Decision-ID":self.decision_id,
                "X-Authorization-Policy-SHA256":"sha256:"+POLICY_SHA256,
                "X-Authorization-Engine":"cedar-policy/"+VERSION}

    def require(self, identity, project_id, *, action, identifier, tenant_id, resource_project_id, rights):
        outcome=self.authorizer.evaluate(identity,project_id,action=action,identifier=identifier,tenant_id=tenant_id,
                                         resource_project_id=resource_project_id,rights=rights)
        if outcome=="allowed":
            return
        reason={"denied":"permission_denied","unavailable":"authorization_unavailable"}.get(outcome,"authorization_error")
        code="LICENCE_USE_BLOCKED" if outcome=="denied" else "AUTHORIZATION_UNAVAILABLE" if outcome=="unavailable" else "AUTHORIZATION_ERROR"
        message="Source permission policy denied this operation" if outcome=="denied" else "Source permission policy is unavailable or invalid; the operation was blocked"
        proof={"decision_id":self.decision_id,"decision":"Deny","action":action if action in ACTIONS else "unknown",
               "reason":reason,"policy_id":POLICY_ID,"policy_sha256":"sha256:"+POLICY_SHA256,
               "engine":"cedar-policy","engine_version":VERSION}
        raise HTTPException(status_code=403 if outcome=="denied" else 503,
                            detail={"code":code,"message":message,"authorization":proof},headers=self.headers())
