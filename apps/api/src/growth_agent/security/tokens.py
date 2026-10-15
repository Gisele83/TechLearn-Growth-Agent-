"""Jetons de session signés.

Implémentation HMAC-SHA256 autonome (format JWT compact) afin de ne pas
dépendre d'une bibliothèque externe pour une fonction aussi critique. La clé
vient de la configuration ; elle n'est jamais journalisée.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_TTL_MINUTES = 12 * 60


class TokenError(Exception):
    """Jeton absent, malformé, mal signé ou expiré."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(message: bytes, secret: str) -> str:
    return _b64url_encode(hmac.new(secret.encode(), message, hashlib.sha256).digest())


def create_access_token(
    *,
    subject: str,
    role: str,
    secret: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    extra: dict[str, Any] | None = None,
) -> str:
    if not secret:
        raise TokenError("Clé de signature absente : impossible d'émettre un jeton.")
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    if extra:
        payload.update(extra)
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(payload, sort_keys=True).encode())
    message = f"{header}.{body}".encode()
    return f"{header}.{body}.{_sign(message, secret)}"


def decode_access_token(token: str, *, secret: str) -> dict[str, Any]:
    if not token or token.count(".") != 2:
        raise TokenError("Jeton malformé.")
    header_b64, body_b64, signature = token.split(".")
    expected = _sign(f"{header_b64}.{body_b64}".encode(), secret)
    if not hmac.compare_digest(expected, signature):
        raise TokenError("Signature invalide.")
    try:
        payload: dict[str, Any] = json.loads(_b64url_decode(body_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("Charge utile illisible.") from exc
    if int(payload.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        raise TokenError("Jeton expiré.")
    return payload
