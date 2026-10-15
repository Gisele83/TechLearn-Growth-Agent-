"""Normalisation et dédoublonnage.

Le dédoublonnage repose sur une clé déterministe plutôt que sur une
comparaison floue : le même prospect produit toujours la même clé, ce qui rend
la contrainte d'unicité en base suffisante et le comportement prévisible.

Ordre de préférence : domaine web > email professionnel > nom normalisé + pays.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

# Fournisseurs de messagerie grand public : un email chez l'un d'eux ne dit
# rien de l'organisation, il ne peut donc pas servir de clé.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.fr",
        "hotmail.com",
        "hotmail.fr",
        "outlook.com",
        "outlook.fr",
        "live.fr",
        "icloud.com",
        "orange.fr",
        "free.fr",
        "wanadoo.fr",
        "protonmail.com",
        "proton.me",
        "yandex.com",
    }
)

_LEGAL_SUFFIXES = (
    "sarl", "sa", "sas", "sasu", "eurl", "sci", "snc", "gie", "scop",
    "ltd", "llc", "inc", "plc", "gmbh", "bv", "nv", "spa", "srl",
    "etablissement", "etablissements", "ets", "societe", "ste", "cie",
)

_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
#: Sigles pointés — « S.A.S. », « S.A.R.L. » — recollés avant le nettoyage,
#: sans quoi ils se décomposeraient en lettres isolées qu'on ne reconnaîtrait
#: plus comme une forme juridique.
_DOTTED_ACRONYM = re.compile(r"\b(?:[a-z]\.){2,}")


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_domain(value: str | None) -> str | None:
    """Extrait un domaine canonique depuis une URL ou un domaine brut."""
    if not value:
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    host = (urlparse(raw).hostname or "").strip(".")
    if not host or "." not in host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def normalize_email(value: str | None) -> str | None:
    """Minuscule + espaces retirés. Pas de normalisation agressive : retirer
    les points ou les suffixes `+tag` change l'adresse chez bien des serveurs,
    et une opposition doit porter sur l'adresse exacte communiquée."""
    if not value:
        return None
    email = value.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return None
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        return None
    return f"{local}@{domain}"


def email_domain(value: str | None) -> str | None:
    email = normalize_email(value)
    return email.split("@", 1)[1] if email else None


def is_public_email_domain(domain: str | None) -> bool:
    return bool(domain) and domain in PUBLIC_EMAIL_DOMAINS


def normalize_organization_name(value: str) -> str:
    """Nom comparable : sans accent, sans ponctuation, sans forme juridique."""
    lowered = strip_accents(value or "").lower()
    lowered = _DOTTED_ACRONYM.sub(lambda m: m.group(0).replace(".", ""), lowered)
    name = _NON_ALNUM.sub(" ", lowered)
    tokens = [t for t in _WHITESPACE.split(name) if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens).strip()


def build_dedupe_key(
    *,
    organization_name: str,
    website: str | None = None,
    domain: str | None = None,
    contact_email: str | None = None,
    country_code: str = "FR",
) -> str:
    """Clé de dédoublonnage stable, préfixée par son origine.

    Le préfixe (`domain:`, `name:`) rend la clé lisible en base et évite qu'un
    nom d'organisation ne collisionne par hasard avec un domaine.
    """
    resolved = normalize_domain(domain) or normalize_domain(website)
    if not resolved:
        candidate = email_domain(contact_email)
        if candidate and not is_public_email_domain(candidate):
            resolved = candidate
    if resolved:
        return f"domain:{resolved}"

    normalized_name = normalize_organization_name(organization_name)
    if not normalized_name:
        raise ValueError(
            "Impossible de calculer une clé de dédoublonnage : ni domaine ni nom exploitable."
        )
    return f"name:{(country_code or 'XX').upper()}:{normalized_name}"
