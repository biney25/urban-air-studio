"""Bounded report capabilities, separate from normal planner/member identity.

Only a token digest is persisted. Every operation holds the Store mutation lock
while checking expiry/revocation, frozen content and the existing export gate.
This is local report review, not a production organisation membership system.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import Field, StrictInt, model_validator

from api.auth import Identity
from api.schemas import Input
from api.storage import Store, digest, new_id


TOKEN_PREFIX = "uarv1_"
TOKEN_PATTERN = re.compile(r"uarv1_[A-Za-z0-9_-]{64}\Z")
GRANT_KIND = "report_review_grant"
KEY_KIND = "report_review_key"
REVOKE_KIND = "report_review_revoke"
COMMENT_KIND = "report_review_comment"
MAX_GRANTS = 100
MAX_COMMENTS = 500
PERMISSIONS = {"read_report": True, "comment": True, "download_html": True}


class ReviewGrantRequest(Input):
    label: Annotated[str, Field(min_length=1, max_length=120)] = "Report reviewer"
    ttl_seconds: Annotated[StrictInt, Field(ge=60, le=604800)] = 86400


class ReviewAnchor(Input):
    kind: Literal["report", "receptor"]
    receptor_id: Annotated[str | None, Field(min_length=1, max_length=100)] = None

    @model_validator(mode="after")
    def location(self):
        if (self.kind == "receptor") != (self.receptor_id is not None):
            raise ValueError("A receptor anchor requires its receptor identity; a report anchor has none")
        return self


class ReviewCommentRequest(Input):
    report_checksum: Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
    anchor: ReviewAnchor
    body: Annotated[str, Field(min_length=1, max_length=2000)]


class TransactionStore:
    """Keep existing nested permission/dependency reads on the held transaction."""
    def __init__(self, store, db):
        self.store, self.db = store, db

    def get(self, kind, identifier, tenant, db=None):
        return self.store.get(kind, identifier, tenant, self.db)

    def run(self, identifier, tenant=None, db=None):
        return self.store.run(identifier, tenant, self.db)

    def __getattr__(self, name):
        return getattr(self.store, name)


def clock_now():
    return datetime.now(timezone.utc).timestamp()


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def sealed(record):
    return {**record, "content_hash": digest(record)}


def make_router(*, identity_dependency, store_dependency, required, error, checked_report, now=clock_now):
    router = APIRouter(tags=["Frozen report review"])

    def integrity(record):
        if not record or digest({k: v for k, v in record.items() if k != "content_hash"}) != record.get("content_hash"):
            error("REVIEW_INTEGRITY_ERROR", "The frozen review record failed integrity checks", 503)
        return record

    def rows(db, kind, tenant, project_id):
        return [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM objects WHERE kind=? AND tenant_id=? AND project_id=? ORDER BY id",
            (kind, tenant, project_id)).fetchall()]

    def comments(db, grant):
        found = []
        for raw in rows(db, COMMENT_KIND, grant["tenant_id"], grant["project_id"]):
            if raw.get("report_id") != grant["report_id"]:
                continue
            item = integrity(raw)
            if item["report_checksum"] != grant["report_checksum"]:
                error("REVIEW_INTEGRITY_ERROR", "Comment refers to a different report version", 503)
            found.append({k: item[k] for k in ("id", "grant_id", "label", "report_id", "report_checksum", "anchor", "body", "created_at")})
        return sorted(found, key=lambda item: (item["created_at"], item["id"]))

    def revoked(store, db, grant):
        raw = store.get(REVOKE_KIND, grant["id"], grant["tenant_id"], db)
        if raw is None:
            return None
        record = integrity(raw)
        if (record["grant_hash"] != grant["content_hash"] or record["project_id"] != grant["project_id"]
                or record["report_id"] != grant["report_id"]):
            error("REVIEW_INTEGRITY_ERROR", "Revocation refers to a different review grant", 503)
        return record["revoked_at"]

    def public_grant(store, db, grant, check_comments=True):
        revocation = revoked(store, db, grant)
        # A corrupt comment must fail closed for reads, but cannot prevent the
        # owner from revoking the grant that would otherwise reach that report.
        records = comments(db, grant) if check_comments else rows(db, COMMENT_KIND, grant["tenant_id"], grant["project_id"])
        return {k: grant[k] for k in ("id", "label", "report_id", "report_checksum", "created_at", "expires_at")} | {
            "revoked_at": revocation, "state": "revoked" if revocation else "expired" if now() >= grant["expires_epoch"] else "active",
            "comment_count": sum(item.get("grant_id") == grant["id"] and item.get("report_id") == grant["report_id"] for item in records)}

    def report_bound(store, db, grant):
        scoped = TransactionStore(store, db)
        owner = Identity(grant["tenant_id"], grant["owner_user_id"])
        required(scoped, "project", grant["project_id"], owner)
        report = required(scoped, "report", grant["report_id"], owner)
        if (report.get("project_id") != grant["project_id"] or report.get("checksum") != grant["report_checksum"]
                or report.get("_html_sha256") != grant["html_sha256"]):
            error("REVIEW_REPORT_CHANGED", "This grant refers to another frozen report version", 409)
        checked = checked_report(scoped, report, owner)
        if (not isinstance(checked.get("_html"), str) or not checked["_html"]
                or hashlib.sha256(checked["_html"].encode("utf-8")).hexdigest() != grant["html_sha256"]):
            error("REPORT_INTEGRITY_ERROR", "The frozen report document failed integrity checks", 503)
        return checked

    def owner_report(store, db, project_id, report_id, identity, check=True):
        scoped = TransactionStore(store, db)
        required(scoped, "project", project_id, identity)
        report = required(scoped, "report", report_id, identity)
        if report.get("project_id") != project_id:
            error("NOT_FOUND", "Report not found in this project", 404)
        return checked_report(scoped, report, identity) if check else report

    def require_capability(request, store, db):
        # A missing token can never fall back to loopback/Cognito planner trust.
        if request.query_params:
            error("REVIEW_QUERY_DENIED", "Review credentials and scope must not be supplied in URLs", 400)
        parts = request.headers.get("authorization", "").split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not TOKEN_PATTERN.fullmatch(parts[1]):
            error("REVIEW_TOKEN_REQUIRED", "A report review credential is required", 401)
        fingerprint = token_digest(parts[1])
        row = db.execute("SELECT tenant_id,project_id,data FROM objects WHERE kind=? AND id=?",
                         (KEY_KIND, "review_key_" + fingerprint)).fetchone()
        if row is None:
            error("REVIEW_TOKEN_INVALID", "The report review credential is invalid or unavailable", 401)
        key = integrity(json.loads(row[2]))
        if key["tenant_id"] != row[0] or key["project_id"] != row[1] or key["token_digest"] != fingerprint:
            error("REVIEW_INTEGRITY_ERROR", "Review credential scope failed integrity checks", 503)
        grant = integrity(store.get(GRANT_KIND, key["grant_id"], key["tenant_id"], db))
        if any(grant[field] != key[field] for field in ("tenant_id", "project_id", "report_id", "token_digest")):
            error("REVIEW_INTEGRITY_ERROR", "Review credential does not match its frozen grant", 503)
        if revoked(store, db, grant):
            error("REVIEW_REVOKED", "This report review grant has been revoked", 403)
        if now() >= grant["expires_epoch"]:
            error("REVIEW_EXPIRED", "This report review grant has expired", 403)
        report = report_bound(store, db, grant)
        if now() >= grant["expires_epoch"]:
            error("REVIEW_EXPIRED", "This report review grant has expired", 403)
        return grant, report

    @router.post("/api/projects/{project_id}/reports/{report_id}/review-grants", status_code=201)
    def create_grant(project_id: str, report_id: str, body: ReviewGrantRequest,
                     identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            report = owner_report(store, db, project_id, report_id, identity)
            if (not report.get("_html_sha256") or not isinstance(report.get("_html"), str)
                    or hashlib.sha256(report["_html"].encode("utf-8")).hexdigest() != report["_html_sha256"]):
                error("REPORT_INTEGRITY_ERROR", "A checked frozen report document is required for review", 503)
            existing = [item for item in rows(db, GRANT_KIND, identity.tenant_id, project_id) if item.get("report_id") == report_id]
            if len(existing) >= MAX_GRANTS:
                error("REVIEW_GRANT_LIMIT", "This report has reached its bounded review grant limit", 409)
            token = TOKEN_PREFIX + secrets.token_urlsafe(48)
            created = now()
            grant = sealed({"schema_version": "report-review-grant-v1", "id": new_id("review"),
                "tenant_id": identity.tenant_id, "owner_user_id": identity.user_id, "project_id": project_id,
                "report_id": report_id, "report_checksum": report["checksum"], "html_sha256": report["_html_sha256"],
                "label": body.label, "token_digest": token_digest(token), "created_at": timestamp(created),
                "expires_at": timestamp(created + body.ttl_seconds), "expires_epoch": created + body.ttl_seconds})
            key = sealed({"id": "review_key_" + grant["token_digest"], "tenant_id": identity.tenant_id,
                "project_id": project_id, "report_id": report_id, "grant_id": grant["id"], "token_digest": grant["token_digest"]})
            Store.put(db, GRANT_KIND, identity.tenant_id, grant, project_id)
            Store.put(db, KEY_KIND, identity.tenant_id, key, project_id)
            return {**public_grant(store, db, grant), "token": token}

    @router.get("/api/projects/{project_id}/reports/{report_id}/review-grants")
    def list_grants(project_id: str, report_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            owner_report(store, db, project_id, report_id, identity)
            grants = [integrity(item) for item in rows(db, GRANT_KIND, identity.tenant_id, project_id) if item.get("report_id") == report_id]
            return [public_grant(store, db, grant) for grant in sorted(grants, key=lambda item: (item["created_at"], item["id"]))]

    @router.post("/api/projects/{project_id}/reports/{report_id}/review-grants/{grant_id}/revoke")
    def revoke_grant(project_id: str, report_id: str, grant_id: str,
                     identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            # Rights withdrawal must not prevent an owner from revoking a grant.
            owner_report(store, db, project_id, report_id, identity, check=False)
            grant = store.get(GRANT_KIND, grant_id, identity.tenant_id, db)
            if not grant or grant.get("project_id") != project_id or grant.get("report_id") != report_id:
                error("NOT_FOUND", "Review grant not found in this report", 404)
            integrity(grant)
            if not revoked(store, db, grant):
                record = sealed({"id": grant_id, "project_id": project_id, "report_id": report_id,
                                 "grant_hash": grant["content_hash"], "revoked_at": timestamp(now())})
                Store.put(db, REVOKE_KIND, identity.tenant_id, record, project_id)
            return public_grant(store, db, grant, check_comments=False)

    @router.get("/api/projects/{project_id}/reports/{report_id}/review-comments")
    def owner_comments(project_id: str, report_id: str, identity=Depends(identity_dependency), store=Depends(store_dependency)):
        with store.transaction() as db:
            report = owner_report(store, db, project_id, report_id, identity)
            return comments(db, {"tenant_id": identity.tenant_id, "project_id": project_id,
                                 "report_id": report_id, "report_checksum": report["checksum"]})

    @router.get("/api/review/report")
    def read_report(request: Request, store=Depends(store_dependency)):
        with store.transaction() as db:
            grant, report = require_capability(request, store, db)
            selected = {k: report[k] for k in ("id", "title", "status", "created_at", "checksum", "comparison", "limitations")}
            selected["planning_question"] = report.get("manifest", {}).get("planning_question")
            selected["source_notices"] = [{key: package["source_compliance"][key]
                for key in ("notice", "licence_url", "attribution_url")}
                for package in report.get("manifest", {}).get("source_export_packages", []) if package.get("source_compliance")]
            return {"grant": public_grant(store, db, grant), "report": selected,
                    "permissions": dict(PERMISSIONS), "comments": comments(db, grant)}

    @router.get("/api/review/report/comments")
    def read_comments(request: Request, store=Depends(store_dependency)):
        with store.transaction() as db:
            grant, _ = require_capability(request, store, db)
            return comments(db, grant)

    @router.post("/api/review/report/comments", status_code=201)
    def append_comment(request: Request, body: ReviewCommentRequest, store=Depends(store_dependency)):
        with store.transaction() as db:
            grant, report = require_capability(request, store, db)
            if body.report_checksum != grant["report_checksum"]:
                error("REVIEW_VERSION_MISMATCH", "Comment must refer to this exact frozen report", 409)
            if body.anchor.kind == "receptor" and body.anchor.receptor_id not in {
                    item["receptor_id"] for item in report["comparison"]["metrics"]}:
                error("REVIEW_ANCHOR_INVALID", "Assessment location is not part of this frozen report", 422)
            if len(comments(db, grant)) >= MAX_COMMENTS:
                error("REVIEW_COMMENT_LIMIT", "This report has reached its bounded comment limit", 409)
            item = sealed({"id": new_id("comment"), "grant_id": grant["id"], "label": grant["label"],
                "report_id": grant["report_id"], "report_checksum": grant["report_checksum"],
                "anchor": body.anchor.model_dump(exclude_none=True), "body": body.body, "created_at": timestamp(now())})
            Store.put(db, COMMENT_KIND, grant["tenant_id"], item, grant["project_id"])
            return {k: v for k, v in item.items() if k != "content_hash"}

    @router.get("/api/review/report/download")
    def download_report(request: Request, store=Depends(store_dependency)):
        with store.transaction() as db:
            _, report = require_capability(request, store, db)
            # No JSON, raw source/scenario/run or arbitrary export is delegated.
            return HTMLResponse(report["_html"], headers={"Content-Disposition": 'attachment; filename="reviewed-report.html"'})

    return router
