"""Small, dependency-light authentication primitives for the v2 API.

The legacy dashboard remains usable in local development. New v2 routes can
require an HS256 bearer token and always derive the tenant from its ``sub``
claim rather than from a caller-controlled header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_token(user_id: str, *, secret: str | None = None, ttl_seconds: int = 3600,
                extra: dict[str, Any] | None = None) -> str:
    key = (secret or os.getenv("SMARTAPPLY_JWT_SECRET") or "").encode()
    if len(key) < 32:
        raise ValueError("SMARTAPPLY_JWT_SECRET must contain at least 32 bytes")
    now = int(time.time())
    payload: dict[str, Any] = {"sub": str(user_id), "iat": now, "exp": now + int(ttl_seconds)}
    payload.update(extra or {})
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(key, f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{_b64(signature)}"


def verify_token(token: str, *, secret: str | None = None, now: int | None = None) -> dict[str, Any]:
    key = (secret or os.getenv("SMARTAPPLY_JWT_SECRET") or "").encode()
    if len(key) < 32:
        raise ValueError("JWT secret is not configured")
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise ValueError("malformed bearer token")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = hmac.new(key, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64(parts[2])):
        raise ValueError("invalid bearer token")
    header = json.loads(_unb64(parts[0]))
    if header.get("alg") != "HS256":
        raise ValueError("unsupported JWT algorithm")
    payload = json.loads(_unb64(parts[1]))
    current = int(time.time() if now is None else now)
    if int(payload.get("exp", 0)) <= current:
        raise ValueError("expired bearer token")
    if not payload.get("sub"):
        raise ValueError("token has no subject")
    return payload


@dataclass(frozen=True)
class ExtensionChallenge:
    nonce: str
    expires_at: float


def new_extension_challenge(ttl_seconds: int = 60) -> ExtensionChallenge:
    return ExtensionChallenge(secrets.token_urlsafe(24), time.time() + ttl_seconds)


def verify_extension_proof(challenge: ExtensionChallenge, proof: str, extension_id: str,
                           secret: str) -> bool:
    if time.time() >= challenge.expires_at:
        return False
    expected = hmac.new(
        secret.encode(), f"{extension_id}:{challenge.nonce}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, proof or "")
