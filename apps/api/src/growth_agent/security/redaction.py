"""Masquage des données sensibles avant journalisation.

Règle : un log ne doit jamais permettre de reconstituer une adresse email
complète, un numéro de téléphone, un jeton ou une clé. On conserve assez de
contexte pour diagnostiquer (domaine, longueur, condensat) et rien de plus.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{7,}\d)(?!\w)")
SECRET_RE = re.compile(
    r"\b(?:sk|pat|ghp|gho|xox[baprs]|AIza|hubspot|Bearer)[-_A-Za-z0-9]{8,}\b",
    re.IGNORECASE,
)

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "private_key",
    "webhook_secret",
    "smtp_password",
}


def digest(value: Any, *, length: int = 16) -> str:
    """Condensat stable et non réversible, pour rapprocher sans exposer."""
    raw = value if isinstance(value, str) else repr(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def redact_email(value: str) -> str:
    """`marie.dupont@ecole.cm` -> `m***@ecole.cm`."""

    def _sub(match: re.Match[str]) -> str:
        return f"{match.group(1)}***@{match.group(3)}"

    return EMAIL_RE.sub(_sub, value)


def redact_text(value: str) -> str:
    """Masque emails, téléphones et motifs de secrets dans un texte libre."""
    if not value:
        return value
    out = SECRET_RE.sub("[secret-masqué]", value)
    out = redact_email(out)
    out = PHONE_RE.sub("[téléphone-masqué]", out)
    return out


def safe_log_payload(payload: Any, *, _depth: int = 0) -> Any:
    """Prépare une structure pour la journalisation.

    Les clés sensibles sont remplacées par un condensat, les chaînes passent par
    `redact_text`, et la profondeur est bornée pour éviter les logs géants.
    """
    if _depth > 6:
        return "[profondeur-maximale]"
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if str(key).lower() in SENSITIVE_KEYS:
                cleaned[key] = f"[masqué:{digest(value, length=8)}]" if value else "[vide]"
            else:
                cleaned[key] = safe_log_payload(value, _depth=_depth + 1)
        return cleaned
    if isinstance(payload, (list, tuple)):
        return [safe_log_payload(item, _depth=_depth + 1) for item in payload[:50]]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload
