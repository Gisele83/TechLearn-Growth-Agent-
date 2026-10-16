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
# Un numéro de téléphone ressemble beaucoup à une date ou à un horodatage.
# Le motif seul masquait « 2026-10-16 20:16:37 », ce qui rendait les journaux
# illisibles. On le double donc d'une validation sur la suite de chiffres.
PHONE_RE = re.compile(r"(?<![\w/:,-])(\+?\d[\d\s().-]{7,}\d)(?![\w/:-])")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}")
#: Longueurs plausibles, indicatif compris (E.164 plafonne à 15).
PHONE_MIN_DIGITS = 9
PHONE_MAX_DIGITS = 15
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


def _looks_like_phone(candidate: str) -> bool:
    """Écarte les dates, versions et horodatages.

    Masquer un faux positif n'est pas anodin : un journal sans horodatage ne
    sert plus à diagnostiquer quoi que ce soit.
    """
    raw = candidate.strip()
    if ISO_DATE_RE.match(raw):
        return False
    digits = re.sub(r"\D", "", raw)
    if not (PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS):
        return False
    # Une longue suite de chiffres sans séparateur est plus souvent un numéro
    # de commande, un SIRET ou un identifiant qu'un téléphone. On exige donc
    # un indice de mise en forme : indicatif, séparateur, ou préfixe national.
    has_separator = bool(re.search(r"[\s().-]", raw))
    return raw.startswith("+") or has_separator or (digits.startswith("0") and len(digits) == 10)


def redact_text(value: str) -> str:
    """Masque emails, téléphones et motifs de secrets dans un texte libre."""
    if not value:
        return value
    out = SECRET_RE.sub("[secret-masqué]", value)
    out = redact_email(out)
    out = PHONE_RE.sub(
        lambda m: "[téléphone-masqué]" if _looks_like_phone(m.group(0)) else m.group(0),
        out,
    )
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
