"""Liste d'opposition.

Règle absolue : un contact opposé ne reçoit plus rien. La vérification porte
sur l'adresse exacte ET sur son domaine, et une opposition de portée `global`
couvre les deux produits.

Le contrôle est centralisé dans `check_suppression` ; aucun autre module n'a le
droit de décider qu'un envoi est permis.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from growth_agent.models.enums import SuppressionReason, SuppressionScope
from growth_agent.models.outreach import SuppressionEntry
from growth_agent.services.dedupe import email_domain, normalize_domain, normalize_email


@dataclass(slots=True)
class SuppressionVerdict:
    """Résultat d'un contrôle d'opposition."""

    suppressed: bool
    reason: str = ""
    matched_value: str = ""
    scope: str = ""
    entry_id: uuid.UUID | None = None

    @property
    def allowed(self) -> bool:
        return not self.suppressed

    def as_dict(self) -> dict[str, object]:
        return {
            "suppressed": self.suppressed,
            "reason": self.reason,
            "matched_value": self.matched_value,
            "scope": self.scope,
            "entry_id": str(self.entry_id) if self.entry_id else None,
        }


def check_suppression(
    session: Session,
    *,
    email: str | None,
    product_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> SuppressionVerdict:
    """Un email vide est traité comme bloquant : on n'écrit pas dans le vide."""
    normalized = normalize_email(email)
    if not normalized:
        return SuppressionVerdict(
            suppressed=True,
            reason="invalid_recipient",
            matched_value="",
            scope="",
        )

    moment = now or datetime.now(UTC)
    domain = email_domain(normalized)
    candidates = [normalized]
    if domain:
        candidates.append(domain)

    statement = select(SuppressionEntry).where(
        SuppressionEntry.normalized_value.in_(candidates),
        SuppressionEntry.effective_from <= moment,
    )
    for entry in session.execute(statement).scalars():
        if entry.scope == SuppressionScope.GLOBAL:
            return _verdict(entry)
        if entry.scope == SuppressionScope.PRODUCT and entry.product_id == product_id:
            return _verdict(entry)

    return SuppressionVerdict(suppressed=False)


def _verdict(entry: SuppressionEntry) -> SuppressionVerdict:
    return SuppressionVerdict(
        suppressed=True,
        reason=entry.reason,
        matched_value=entry.normalized_value,
        scope=entry.scope,
        entry_id=entry.id,
    )


def add_suppression(
    session: Session,
    *,
    value: str,
    reason: str = SuppressionReason.OPT_OUT,
    scope: str = SuppressionScope.GLOBAL,
    product_id: uuid.UUID | None = None,
    source: str = "manual",
    note: str = "",
    value_type: str | None = None,
    now: datetime | None = None,
) -> SuppressionEntry:
    """Ajoute une opposition (idempotent : réajouter la même ne duplique pas).

    Accepte une adresse ou un domaine entier ; le type est déduit de la valeur.
    """
    moment = now or datetime.now(UTC)
    normalized = normalize_email(value)
    resolved_type = "email"
    if not normalized:
        normalized = normalize_domain(value)
        resolved_type = "domain"
    if not normalized:
        raise ValueError(f"Valeur d'opposition inexploitable : {value!r}")
    resolved_type = value_type or resolved_type

    if scope == SuppressionScope.PRODUCT and product_id is None:
        raise ValueError("Une opposition de portée 'product' exige un produit.")
    if scope == SuppressionScope.GLOBAL:
        product_id = None

    existing = _find_entry(session, normalized, scope, product_id)
    if existing is not None:
        return existing

    entry = SuppressionEntry(
        product_id=product_id,
        scope=scope,
        value_type=resolved_type,
        normalized_value=normalized,
        reason=reason,
        source=source,
        note=note,
        effective_from=moment,
    )
    # La lecture ci-dessus ne suffit pas : deux clics simultanés sur un même
    # lien de désinscription la franchissent ensemble. Le point de reprise
    # permet de rattraper le perdant sans perdre la transaction en cours —
    # il relit, et renvoie l'opposition que l'autre vient de créer.
    try:
        with session.begin_nested():
            session.add(entry)
            session.flush()
    except IntegrityError:
        existing = _find_entry(session, normalized, scope, product_id)
        if existing is None:  # pragma: no cover - contrainte violée pour une autre raison
            raise
        return existing
    return entry


def _find_entry(
    session: Session, normalized: str, scope: str, product_id: uuid.UUID | None
) -> SuppressionEntry | None:
    statement = select(SuppressionEntry).where(
        SuppressionEntry.normalized_value == normalized,
        SuppressionEntry.scope == scope,
    )
    statement = statement.where(
        SuppressionEntry.product_id.is_(None)
        if product_id is None
        else SuppressionEntry.product_id == product_id
    )
    return session.execute(statement.limit(1)).scalar_one_or_none()


def list_suppressions(
    session: Session, *, product_id: uuid.UUID | None = None, limit: int = 200
) -> list[SuppressionEntry]:
    statement = select(SuppressionEntry).order_by(SuppressionEntry.created_at.desc()).limit(limit)
    entries = list(session.execute(statement).scalars())
    if product_id is None:
        return entries
    return [e for e in entries if e.scope == SuppressionScope.GLOBAL or e.product_id == product_id]
