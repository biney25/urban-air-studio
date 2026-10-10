"""Explicit local identity or independently verified Cognito access tokens.

No secret is sent to the browser. The issuer fixes the trusted JWKS location;
token headers cannot select a network URL or a different signing algorithm.
"""
from dataclasses import dataclass
from functools import lru_cache
import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
import jwt


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str


def reject(code, message, status):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def origin_of(url):
    value = urlsplit(url)
    if value.username or value.password or not value.hostname:
        raise ValueError("Invalid origin")
    return value.scheme, value.hostname, value.port


def secure_url(url, loopback=False):
    value = urlsplit(url)
    origin_of(url)
    if value.fragment or value.query or value.scheme != "https":
        if not (loopback and value.scheme == "http" and value.hostname in {"localhost", "127.0.0.1", "::1"}
                and not value.fragment and not value.query):
            raise ValueError("An HTTPS endpoint is required")
    return value


def public_config():
    mode = os.getenv("UA_MODE", "local")
    if mode == "local":
        return {"mode": "local", "configured": True}
    if mode != "cognito":
        return {"mode": mode, "configured": False}
    issuer = os.getenv("UA_COGNITO_ISSUER", "").rstrip("/")
    client = os.getenv("UA_COGNITO_CLIENT_ID", "")
    domain = os.getenv("UA_COGNITO_DOMAIN", "").rstrip("/")
    redirect = os.getenv("UA_COGNITO_REDIRECT_URI", "")
    logout = os.getenv("UA_COGNITO_LOGOUT_URI", "")
    try:
        if not re.fullmatch(r"https://cognito-idp\.[a-z]{2}(?:-[a-z]+)+-\d\.amazonaws\.com(?:\.cn)?/[a-z]{2}(?:-[a-z]+)+-\d_[A-Za-z0-9]+", issuer):
            raise ValueError("Invalid fixed Cognito issuer")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client):
            raise ValueError("Invalid app client")
        if secure_url(domain).path not in {"", "/"}:
            raise ValueError("Domain must be an origin")
        if secure_url(redirect, True).path != "/auth/callback":
            raise ValueError("Invalid callback")
        secure_url(logout, True)
        if origin_of(redirect) != origin_of(logout):
            raise ValueError("Callback and logout origins differ")
    except (ValueError, TypeError):
        return {"mode": "cognito", "configured": False}
    return {"mode": mode, "configured": True, "issuer": issuer, "client_id": client,
            "authorization_endpoint": domain + "/oauth2/authorize", "token_endpoint": domain + "/oauth2/token",
            "logout_endpoint": domain + "/logout", "redirect_uri": redirect, "logout_uri": logout,
            "scopes": ["openid", "email", "profile"]}


@lru_cache(maxsize=4)
def jwks_client(issuer):
    # Key-set caching, bounded fetch timeout and unknown-kid cooldown protect
    # rotation without creating an indefinitely trusted individual-key cache.
    return jwt.PyJWKClient(issuer + "/.well-known/jwks.json", lifespan=300,
                           timeout=5, cache_keys=False, cooldown_duration=30)


def request_identity(request: Request):
    config = public_config()
    if not config["configured"]:
        reject("AUTH_NOT_CONFIGURED", "Deployment identity settings are incomplete; access is closed", 503)
    if config["mode"] == "local":
        peer = request.client.host if request.client else ""
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        origin = request.headers.get("origin")
        if peer not in {"127.0.0.1", "::1"} or host not in {"localhost", "127.0.0.1", "::1"}:
            reject("LOCAL_IDENTITY_ONLY", "Development identity requires a loopback connection", 403)
        if origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            reject("LOCAL_ORIGIN_REQUIRED", "Remote browser origins are denied", 403)
        return Identity("local-development", "local-planner")
    origin = request.headers.get("origin")
    if origin:
        try:
            same_origin = origin_of(origin) == origin_of(config["redirect_uri"])
        except ValueError:
            same_origin = False
        if not same_origin:
            reject("ORIGIN_DENIED", "This browser origin is not configured for the application", 403)
    authorization = request.headers.get("authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16_384:
        reject("AUTH_REQUIRED", "Sign in to access this study", 401)
    token = parts[1]
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"] or len(header["kid"]) > 256:
            raise jwt.InvalidTokenError("Invalid signing header")
        key = jwks_client(config["issuer"]).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=config["issuer"],
                            options={"require": ["exp", "iat", "iss", "sub", "token_use", "client_id"], "verify_aud": False})
        if (claims["token_use"] != "access" or claims["client_id"] != config["client_id"]
                or not isinstance(claims.get("scope"), str) or "openid" not in claims["scope"].split()
                or not isinstance(claims["sub"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", claims["sub"])):
            raise jwt.InvalidTokenError("Invalid access claims")
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        reject("AUTH_INVALID", "Session verification failed or expired; sign in again", 401)
    # A personal tenant is derived only from signed immutable subject identity.
    # Mutable custom attributes cannot move a user into another user's study.
    return Identity("user:" + claims["sub"], claims["sub"])
