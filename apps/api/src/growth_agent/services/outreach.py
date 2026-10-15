"""Service d'envoi : le seul point du code autorisé à déclencher un email.

Six contrôles sont appliqués dans cet ordre, et le premier qui échoue arrête
l'envoi. L'ordre compte : on vérifie l'opposition avant de regarder quoi que ce
soit d'autre, pour qu'un contact opposé ne puisse jamais être traité, même en
cas de bug ailleurs.

    1. destinataire exploitable
    2. liste d'opposition
    3. approbation humaine valide et correspondant au contenu
    4. anti-doublon (pas deux fois le même message au même contact)
    5. fréquence maximale par contact et par campagne
    6. autorisation d'envoi réel (sinon, envoi simulé)

Chaque refus produit un événement `send_blocked` motivé : un blocage est
observable dans les KPI, jamais silencieux.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.email.base import EmailMessage, EmailProvider
from growth_agent.models.enums import DraftStatus, OutreachEventType
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.security.redaction import digest
from growth_agent.services.approvals import check_send_gate
from growth_agent.services.audit import record_audit
from growth_agent.services.suppression import check_suppression


#: Motifs de blocage définitifs : le brouillon ne pourra pas partir en l'état.
#: Les autres motifs (approbation manquante, plafond de fréquence, panne du
#: fournisseur) sont transitoires — ils sont journalisés mais ne condamnent pas
#: le brouillon, qui reste utilisable une fois la cause levée.
TERMINAL_REASONS: frozenset[str] = frozenset(
    {"no_recipient", "suppressed", "duplicate", "already_sent"}
)


class SendBlocked(Exception):
    """L'envoi est refusé par un garde-fou."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(slots=True)
class SendOutcome:
    sent: bool
    reason_code: str = ""
    detail: str = ""
    message_id: str = ""
    simulated: bool = False


def send_draft(
    session: Session,
    *,
    draft: MessageDraft,
    email_provider: EmailProvider,
    actor: str,
    actor_role: str = "",
    settings: Settings | None = None,
    now: datetime | None = None,
    unsubscribe_base_url: str = "https://growth.example.org/opposition",
) -> SendOutcome:
    settings = settings or get_settings()
    moment = now or datetime.now(UTC)

    def _block(code: str, detail: str) -> SendOutcome:
        if code in TERMINAL_REASONS:
            draft.status = DraftStatus.BLOCKED
        draft.blocked_reason = detail
        _record_event(
            session,
            draft=draft,
            event_type=OutreachEventType.SEND_BLOCKED,
            actor=actor,
            moment=moment,
            payload={"reason_code": code, "detail": detail},
        )
        record_audit(
            session,
            actor=actor,
            actor_role=actor_role,
            action="outreach.send_blocked",
            object_type="message_draft",
            object_id=draft.id,
            outcome="blocked",
            metadata={"reason_code": code, "detail": detail},
            now=moment,
        )
        session.flush()
        return SendOutcome(sent=False, reason_code=code, detail=detail)

    # 0. Déjà parti ---------------------------------------------------------
    if draft.status == DraftStatus.SENT:
        return _block("already_sent", "Ce message a déjà été envoyé à ce contact.")

    # 1. Destinataire -------------------------------------------------------
    if not draft.recipient_email:
        return _block("no_recipient", "Aucun destinataire sur ce brouillon.")

    # 2. Opposition ---------------------------------------------------------
    verdict = check_suppression(
        session, email=draft.recipient_email, product_id=draft.product_id, now=moment
    )
    if verdict.suppressed:
        return _block(
            "suppressed",
            f"Contact opposé à la prospection (motif « {verdict.reason} »).",
        )

    # 3. Approbation --------------------------------------------------------
    gate = check_send_gate(session, draft=draft, now=moment)
    if not gate.allowed:
        return _block("not_approved", gate.reason)

    # 4. Anti-doublon -------------------------------------------------------
    content_digest = digest(f"{draft.recipient_email}|{draft.subject}|{draft.body}", length=32)
    already_sent = session.execute(
        select(func.count())
        .select_from(OutreachEvent)
        .where(
            OutreachEvent.event_type == OutreachEventType.SENT,
            OutreachEvent.prospect_id == draft.prospect_id,
            OutreachEvent.idempotency_key == f"send:{content_digest}",
        )
    ).scalar_one()
    if already_sent:
        return _block("duplicate", "Ce message a déjà été envoyé à ce contact.")

    # 5. Fréquence ----------------------------------------------------------
    window_start = moment - timedelta(days=30)
    contact_sends = session.execute(
        select(func.count())
        .select_from(OutreachEvent)
        .where(
            OutreachEvent.event_type == OutreachEventType.SENT,
            OutreachEvent.prospect_id == draft.prospect_id,
            OutreachEvent.occurred_at >= window_start,
        )
    ).scalar_one()
    if contact_sends >= settings.email_max_per_contact_per_30_days:
        return _block(
            "frequency_contact",
            f"Plafond atteint : {contact_sends} message(s) en 30 jours pour ce contact "
            f"(maximum {settings.email_max_per_contact_per_30_days}).",
        )

    if draft.campaign_id:
        day_start = moment - timedelta(days=1)
        campaign_sends = session.execute(
            select(func.count())
            .select_from(OutreachEvent)
            .where(
                OutreachEvent.event_type == OutreachEventType.SENT,
                OutreachEvent.campaign_id == draft.campaign_id,
                OutreachEvent.occurred_at >= day_start,
            )
        ).scalar_one()
        if campaign_sends >= settings.email_max_per_campaign_per_day:
            return _block(
                "frequency_campaign",
                f"Plafond quotidien de la campagne atteint ({campaign_sends}/"
                f"{settings.email_max_per_campaign_per_day}).",
            )

    # 6. Envoi --------------------------------------------------------------
    unsubscribe_url = f"{unsubscribe_base_url}?c={content_digest}"
    message = EmailMessage(
        to_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
        from_email=settings.email_from,
        campaign_id=str(draft.campaign_id or ""),
        product_code=str(draft.product_id),
    )
    message.headers.update(message.unsubscribe_headers(unsubscribe_url))

    result = email_provider.send(message, idempotency_key=f"send:{content_digest}")
    simulated = email_provider.provider == "mock" or not settings.can_send_email()

    if not result.accepted:
        return _block("provider_refused", result.detail or "Le fournisseur a refusé le message.")

    draft.status = DraftStatus.SENT
    draft.sent_at = moment
    draft.blocked_reason = ""
    _record_event(
        session,
        draft=draft,
        event_type=OutreachEventType.SENT,
        actor=actor,
        moment=moment,
        payload={
            "provider": result.provider,
            "simulated": simulated,
            "message_id": result.message_id,
        },
        idempotency_key=f"send:{content_digest}",
    )
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="outreach.sent",
        object_type="message_draft",
        object_id=draft.id,
        metadata={"provider": result.provider, "simulated": simulated},
        payload_for_digest=content_digest,
        now=moment,
    )
    session.flush()
    return SendOutcome(
        sent=True,
        message_id=result.message_id,
        simulated=simulated,
        detail=(
            "Envoi simulé : aucun message n'a quitté la machine."
            if simulated
            else "Message remis au fournisseur."
        ),
    )


def _record_event(
    session: Session,
    *,
    draft: MessageDraft,
    event_type: str,
    actor: str,
    moment: datetime,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> OutreachEvent:
    event = OutreachEvent(
        product_id=draft.product_id,
        campaign_id=draft.campaign_id,
        prospect_id=draft.prospect_id,
        draft_id=draft.id,
        event_type=event_type,
        channel=draft.channel,
        occurred_at=moment,
        actor=actor,
        payload=payload or {},
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event


def record_outreach_event(
    session: Session,
    *,
    product_id: uuid.UUID,
    event_type: str,
    occurred_at: datetime | None = None,
    campaign_id: uuid.UUID | None = None,
    prospect_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
    actor: str = "system",
    channel: str = "email",
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> OutreachEvent:
    """Enregistre un événement de funnel (réponse, démo, pilote, perte…).

    Si `idempotency_key` est déjà connue, l'événement existant est renvoyé tel
    quel : rejouer un webhook ne fausse pas les KPI.
    """
    if idempotency_key:
        existing = session.execute(
            select(OutreachEvent).where(OutreachEvent.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    event = OutreachEvent(
        product_id=product_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        draft_id=draft_id,
        event_type=event_type,
        channel=channel,
        occurred_at=occurred_at or datetime.now(UTC),
        actor=actor,
        payload=payload or {},
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event
