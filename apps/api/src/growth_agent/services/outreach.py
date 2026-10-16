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

Puis l'envoi lui-même suit un protocole en trois temps — réserver, envoyer,
confirmer — décrit dans `send_draft`. Il existe parce qu'un contrôle
anti-doublon qui lit avant d'écrire ne protège de rien : deux processus
concurrents le franchissent ensemble, et un incident entre la remise au
fournisseur et la validation en base fait repartir le message à la reprise.

Chaque refus produit un événement `send_blocked` motivé : un blocage est
observable dans les KPI, jamais silencieux.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.email.base import EmailMessage, EmailProvider
from growth_agent.models.enums import (
    SENT_STATUSES,
    DeliveryState,
    DraftStatus,
    OutreachEventType,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.security.redaction import digest
from growth_agent.services.approvals import check_send_gate
from growth_agent.services.audit import record_audit
from growth_agent.services.suppression import check_suppression
from growth_agent.services.unsubscribe import issue_token

#: Motifs de blocage définitifs : le brouillon ne pourra pas partir en l'état.
#: Les autres motifs (approbation manquante, plafond de fréquence, panne du
#: fournisseur) sont transitoires — ils sont journalisés mais ne condamnent pas
#: le brouillon, qui reste utilisable une fois la cause levée.
TERMINAL_REASONS: frozenset[str] = frozenset(
    {"no_recipient", "suppressed", "duplicate", "already_sent", "uncertain"}
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
    #: État final de l'envoi : sent, failed, uncertain — ou vide si bloqué
    #: avant toute tentative.
    delivery_state: str = ""


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
            # Écriture conditionnelle, pas une affectation en mémoire : un
            # travailleur qui perd la course ne doit pas repasser en
            # « bloqué » un brouillon qu'un autre vient de faire partir. L'essai
            # à douze travailleurs a produit exactement ce cas — message remis,
            # brouillon affiché bloqué — et quelqu'un l'aurait renvoyé à la main.
            session.execute(
                update(MessageDraft)
                .where(
                    MessageDraft.id == draft.id,
                    MessageDraft.status.not_in(SENT_STATUSES),
                )
                .values(status=DraftStatus.BLOCKED, blocked_reason=detail)
            )
            session.expire(draft, ["status", "blocked_reason"])
        else:
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
    if draft.status in SENT_STATUSES:
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

    # --- Mode d'envoi ------------------------------------------------------
    # Déterminé avant la clé d'idempotence : une répétition et un envoi réel
    # portent des clés distinctes, pour qu'un essai en simulation n'empêche
    # pas l'envoi réel du même message.
    simulated = email_provider.provider == "mock" or not settings.can_send_email()
    content_digest = digest(f"{draft.recipient_email}|{draft.subject}|{draft.body}", length=32)
    idempotency_key = f"send:{'sim:' if simulated else ''}{content_digest}"

    # 4. Anti-doublon -------------------------------------------------------
    # Lecture indicative seulement : elle évite un aller-retour inutile, mais
    # ce n'est pas elle qui garantit l'unicité. C'est la réservation ci-dessous.
    existing = session.execute(
        select(OutreachEvent).where(OutreachEvent.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        return _block(*_describe_existing(existing))

    # 5. Fréquence ----------------------------------------------------------
    # Les plafonds ne comptent que les envois réels : une répétition ne doit
    # pas consommer le quota d'un contact.
    window_start = moment - timedelta(days=30)
    contact_sends = session.execute(
        select(func.count())
        .select_from(OutreachEvent)
        .where(
            OutreachEvent.event_type == OutreachEventType.SENT,
            OutreachEvent.prospect_id == draft.prospect_id,
            OutreachEvent.occurred_at >= window_start,
            OutreachEvent.is_simulated.is_(False),
            OutreachEvent.delivery_state != DeliveryState.FAILED,
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
                OutreachEvent.is_simulated.is_(False),
                OutreachEvent.delivery_state != DeliveryState.FAILED,
            )
        ).scalar_one()
        if campaign_sends >= settings.email_max_per_campaign_per_day:
            return _block(
                "frequency_campaign",
                f"Plafond quotidien de la campagne atteint ({campaign_sends}/"
                f"{settings.email_max_per_campaign_per_day}).",
            )

    # --- RÉSERVER ----------------------------------------------------------
    # On inscrit l'intention d'envoyer, et on valide la transaction, AVANT de
    # remettre quoi que ce soit au fournisseur. La contrainte d'unicité sur la
    # clé d'idempotence devient alors un verrou que deux processus concurrents
    # ne peuvent pas franchir ensemble : le perdant échoue ici, sans avoir
    # rien envoyé. Et si le processus meurt juste après, la réservation
    # subsiste : la reprise saura qu'une tentative a eu lieu.
    token = issue_token(
        session,
        email=draft.recipient_email,
        product_id=draft.product_id,
        prospect_id=draft.prospect_id,
        campaign_id=draft.campaign_id,
        draft_id=draft.id,
        now=moment,
    )
    # Le `try` couvre l'écriture ET la validation : la violation d'unicité
    # remonte au premier des deux qui atteint la base — c'est le `flush` de
    # `_record_event` qui l'a levée lors de l'essai à douze travailleurs, pas
    # le `commit`. Ne protéger que le `commit` laissait le perdant sortir en
    # exception, sans événement `send_blocked` : un refus invisible.
    try:
        reservation = _record_event(
            session,
            draft=draft,
            event_type=OutreachEventType.SENT,
            actor=actor,
            moment=moment,
            payload={"provider": email_provider.provider},
            idempotency_key=idempotency_key,
            is_simulated=simulated,
            delivery_state=DeliveryState.RESERVED,
        )
        session.commit()
    except IntegrityError:
        # Un autre processus a réservé entre notre lecture et notre écriture.
        session.rollback()
        return _block("duplicate", "Un envoi concurrent a déjà été réservé pour ce message.")

    # --- ENVOYER -----------------------------------------------------------
    unsubscribe_url = token.url(settings.public_base_url or unsubscribe_base_url)
    message = EmailMessage(
        to_email=draft.recipient_email,
        subject=draft.subject,
        body=draft.body,
        from_email=settings.email_from,
        campaign_id=str(draft.campaign_id or ""),
        product_code=str(draft.product_id),
    )
    message.headers.update(message.unsubscribe_headers(unsubscribe_url))

    try:
        result = email_provider.send(message, idempotency_key=idempotency_key)
    except Exception as exc:
        # Issue inconnue : coupure, délai dépassé, réponse illisible. Le
        # message est peut-être parti. On ne rejoue pas et on n'efface pas —
        # renvoyer un message déjà reçu est pire que de ne rien faire.
        reservation.delivery_state = DeliveryState.UNCERTAIN
        reservation.payload = {
            **reservation.payload,
            "error_type": type(exc).__name__,
        }
        draft.blocked_reason = (
            "Issue d'envoi inconnue : le message est peut-être parti. "
            "Arbitrage humain requis avant toute nouvelle tentative."
        )
        session.commit()
        record_audit(
            session,
            actor=actor,
            actor_role=actor_role,
            action="outreach.send_uncertain",
            object_type="message_draft",
            object_id=draft.id,
            outcome="failure",
            metadata={"error_type": type(exc).__name__},
            now=moment,
        )
        session.commit()
        return SendOutcome(
            sent=False,
            reason_code="uncertain",
            detail=draft.blocked_reason,
            delivery_state=DeliveryState.UNCERTAIN,
        )

    if not result.accepted:
        # Refus explicite : rien n'est parti. On libère la réservation pour
        # qu'une nouvelle tentative reste possible une fois la cause levée.
        reservation.delivery_state = DeliveryState.FAILED
        session.delete(reservation)
        session.commit()
        return _block("provider_refused", result.detail or "Le fournisseur a refusé le message.")

    # --- CONFIRMER ---------------------------------------------------------
    reservation.delivery_state = DeliveryState.SENT
    reservation.provider_message_id = (result.message_id or "")[:255]
    reservation.payload = {
        **reservation.payload,
        "simulated": simulated,
        "message_id": result.message_id,
    }
    draft.status = DraftStatus.SENT_SIMULATED if simulated else DraftStatus.SENT
    draft.sent_at = moment
    draft.blocked_reason = ""

    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="outreach.sent_simulated" if simulated else "outreach.sent",
        object_type="message_draft",
        object_id=draft.id,
        metadata={"provider": result.provider, "simulated": simulated},
        payload_for_digest=content_digest,
        now=moment,
    )
    session.commit()
    return SendOutcome(
        sent=True,
        message_id=result.message_id,
        simulated=simulated,
        delivery_state=DeliveryState.SENT,
        detail=(
            "Envoi simulé : aucun message n'a quitté la machine."
            if simulated
            else "Message remis au fournisseur."
        ),
    )


def _describe_existing(event: OutreachEvent) -> tuple[str, str]:
    """Motif de blocage correspondant à une réservation déjà présente."""
    state = event.delivery_state
    if state == DeliveryState.UNCERTAIN:
        return (
            "uncertain",
            "Une tentative précédente s'est terminée sans réponse lisible du "
            "fournisseur. Arbitrage humain requis avant toute nouvelle tentative.",
        )
    if state == DeliveryState.RESERVED:
        return (
            "duplicate",
            "Un envoi est déjà réservé pour ce message et n'a pas encore abouti.",
        )
    return ("duplicate", "Ce message a déjà été envoyé à ce contact.")


def _record_event(
    session: Session,
    *,
    draft: MessageDraft,
    event_type: str,
    actor: str,
    moment: datetime,
    payload: dict | None = None,
    idempotency_key: str | None = None,
    is_simulated: bool = False,
    delivery_state: str | None = None,
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
        is_simulated=is_simulated,
        delivery_state=delivery_state,
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
