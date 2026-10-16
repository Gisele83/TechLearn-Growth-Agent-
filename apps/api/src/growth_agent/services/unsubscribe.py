"""Jetons de désinscription et traitement du clic.

Le jeton est tiré au hasard, transmis une seule fois dans le message, et
stocké **haché**. L'application ne peut donc pas reconstituer un lien à partir
de sa base : elle peut seulement reconnaître un lien qu'on lui présente.

Le clic est volontairement tolérant. Un jeton inconnu ne révèle rien ; un
jeton déjà utilisé confirme à nouveau sans erreur. Quelqu'un qui cherche à ne
plus être contacté ne doit jamais tomber sur un message d'échec.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.db.base import utcnow
from growth_agent.models.enums import SuppressionReason, SuppressionScope
from growth_agent.models.outreach import UnsubscribeToken
from growth_agent.services.audit import record_audit
from growth_agent.services.suppression import add_suppression

#: 32 octets = 256 bits d'entropie, rendus en 43 caractères URL-sûrs.
TOKEN_BYTES = 32


def hash_token(raw_token: str) -> str:
    """Empreinte stockée. Pas de sel : le jeton a déjà 256 bits d'entropie,
    une attaque par dictionnaire n'a aucun sens ici."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class IssuedToken:
    """Le jeton en clair n'existe qu'ici, le temps de composer le lien."""

    raw_token: str
    record: UnsubscribeToken

    def url(self, public_base_url: str) -> str:
        return f"{public_base_url.rstrip('/')}/unsubscribe/{self.raw_token}"


def issue_token(
    session: Session,
    *,
    email: str,
    product_id: uuid.UUID,
    prospect_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    """Crée un jeton. Le retour contient le clair ; la base n'a que l'empreinte."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    record = UnsubscribeToken(
        token_hash=hash_token(raw),
        prospect_id=prospect_id,
        product_id=product_id,
        campaign_id=campaign_id,
        draft_id=draft_id,
        email=(email or "").strip().lower(),
    )
    session.add(record)
    session.flush()
    _ = now  # horodatage porté par created_at
    return IssuedToken(raw_token=raw, record=record)


def find_token(session: Session, raw_token: str) -> UnsubscribeToken | None:
    if not raw_token or len(raw_token) < 20:
        return None
    return session.execute(
        select(UnsubscribeToken).where(UnsubscribeToken.token_hash == hash_token(raw_token))
    ).scalar_one_or_none()


@dataclass(slots=True)
class UnsubscribeResult:
    accepted: bool
    already_done: bool = False
    reason: str = ""


def process_unsubscribe(
    session: Session,
    *,
    raw_token: str,
    source: str = "unsubscribe_link",
    now: datetime | None = None,
) -> UnsubscribeResult:
    """Traite un clic. Idempotent : recliquer confirme sans erreur."""
    moment = now or utcnow()
    token = find_token(session, raw_token)

    if token is None:
        # Aucun détail : un jeton inconnu ne doit pas permettre de distinguer
        # « jamais émis » de « révoqué », ce qui renseignerait un curieux.
        return UnsubscribeResult(accepted=False, reason="unknown_token")

    if token.revoked_at is not None:
        return UnsubscribeResult(accepted=False, reason="revoked_token")

    already = token.used_at is not None

    add_suppression(
        session,
        value=token.email,
        reason=SuppressionReason.OPT_OUT,
        scope=SuppressionScope.GLOBAL,
        source=source,
        note="Désinscription par le lien du message.",
        now=moment,
    )
    if not already:
        token.used_at = moment

    record_audit(
        session,
        # Pas d'adresse ici : l'audit porte sur le jeton, pas sur la personne.
        actor="visiteur_anonyme",
        action="unsubscribe.accepted",
        object_type="unsubscribe_token",
        object_id=token.id,
        metadata={"already_done": already, "source": source},
        now=moment,
    )
    session.flush()
    return UnsubscribeResult(accepted=True, already_done=already)


def revoke_token(session: Session, token_id: uuid.UUID, *, now: datetime | None = None) -> bool:
    record = session.get(UnsubscribeToken, token_id)
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = now or utcnow()
    session.flush()
    return True
