"""Absorber ce que le fournisseur d'expédition constate.

Trois règles, qui expliquent les refus que ce module oppose.

**Un événement qui ne se rattache à aucun envoi connu est écarté.** Le compte
SendGrid peut servir à autre chose — les réinitialisations de mot de passe de
Maintrix partent de la même adresse d'expéditeur. Absorber ces événements
gonflerait les indicateurs de Growth Agent avec du trafic qui ne le regarde
pas, et rattacherait à des prospects des faits qui concernent des comptes
utilisateurs.

**Le motif en clair n'est jamais stocké.** SendGrid renvoie des phrases du
genre « 550 5.1.1 <direction@college.example.org> user unknown » : l'adresse
y figure. Seul un code est conservé, conformément à la liste blanche de la
politique de rétention.

**Un rejet ne réécrit pas l'envoi.** Il est compté à côté, comme un
rendez-vous manqué. Soustraire les rejets des contacts ferait bouger tout
seuls les chiffres d'un trimestre déjà lu — et un système dont les
indicateurs bougent seuls n'est plus un système de mesure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.connectors.email.sendgrid_events import ProviderEvent
from growth_agent.models.enums import (
    DeliveryState,
    EventSource,
    OutreachEventType,
    SuppressionReason,
    SuppressionScope,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.services.outreach import record_outreach_event
from growth_agent.services.suppression import add_suppression

#: Ce qu'un événement SendGrid devient chez nous. Tout le reste — `processed`,
#: `deferred`, `click` — est écarté : ou bien il ne nous apprend rien que
#: nous ne sachions déjà, ou bien il relève d'un suivi de comportement que ce
#: projet ne pratique pas.
EVENT_MAPPING: dict[str, str] = {
    "delivered": OutreachEventType.DELIVERED,
    "bounce": OutreachEventType.BOUNCED,
    "dropped": OutreachEventType.BOUNCED,
    "open": OutreachEventType.OPENED,
    "spamreport": OutreachEventType.OPTED_OUT,
    "unsubscribe": OutreachEventType.OPTED_OUT,
    "group_unsubscribe": OutreachEventType.OPTED_OUT,
}

#: Événements qui valent opposition, et le motif à inscrire.
#:
#: Growth Agent n'utilise pas la désinscription de SendGrid — chaque message
#: porte son propre lien. Un événement `unsubscribe` ne devrait donc jamais
#: arriver ; s'il arrive, quelqu'un a demandé à ne plus être contacté, et
#: l'ignorer parce qu'il emprunte un chemin inattendu serait indéfendable.
OPPOSITION_EVENTS: dict[str, str] = {
    "spamreport": SuppressionReason.COMPLAINT,
    "unsubscribe": SuppressionReason.OPT_OUT,
    "group_unsubscribe": SuppressionReason.OPT_OUT,
}

#: Type de rejet qui vaut opposition. « blocked » et « deferred » sont
#: temporaires — une boîte pleine se vide. « bounce » est définitif :
#: continuer d'écrire à une adresse qui n'existe pas abîme la réputation
#: d'expédition de tout le domaine, celle de Maintrix comprise.
HARD_BOUNCE = "bounce"


@dataclass
class IngestReport:
    """Ce qu'une livraison a produit. Chaque ligne entrée en ressort comptée."""

    accepted: int = 0
    #: Rattachés à aucun envoi connu de Growth Agent.
    unmatched: int = 0
    #: Type d'événement hors contrat.
    ignored: int = 0
    #: Ouvertures reçues alors que le suivi d'ouverture n'est pas activé.
    opens_discarded: int = 0
    #: Déjà absorbés : SendGrid rejoue une livraison qu'il croit perdue.
    duplicates: int = 0
    oppositions: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return (
            self.accepted + self.unmatched + self.ignored + self.opens_discarded + self.duplicates
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["total"] = self.total
        return payload


def _sent_event(session: Session, message_id: str) -> OutreachEvent | None:
    """L'envoi auquel cet événement se rapporte, s'il est de nous."""
    return session.execute(
        select(OutreachEvent).where(
            OutreachEvent.provider_message_id == message_id,
            OutreachEvent.event_type == OutreachEventType.SENT,
            # Une réservation abandonnée n'a jamais été remise au
            # fournisseur : elle ne peut pas avoir produit d'événement.
            OutreachEvent.delivery_state == DeliveryState.SENT,
        )
    ).scalar_one_or_none()


def ingest_provider_events(
    session: Session,
    *,
    events: list[ProviderEvent],
    settings: Settings,
    now: datetime | None = None,
) -> IngestReport:
    """Absorbe une livraison d'événements d'expédition."""
    moment = now or datetime.now(UTC)
    report = IngestReport()

    for event in events:
        type_local = EVENT_MAPPING.get(event.kind)
        if type_local is None:
            report.ignored += 1
            continue

        # Une ouverture n'existe que si un pixel de suivi a été inséré dans
        # le message. Tant que ce suivi n'est pas décidé, les ouvertures qui
        # arriveraient ne sont pas absorbées — mais elles sont comptées, pour
        # que l'écart se voie au lieu de se deviner.
        if event.kind == "open" and not settings.sendgrid_track_opens:
            report.opens_discarded += 1
            continue

        envoi = _sent_event(session, event.message_id)
        if envoi is None:
            report.unmatched += 1
            continue

        cle = f"sendgrid:{event.event_id}"
        deja = session.execute(
            select(OutreachEvent.id).where(OutreachEvent.idempotency_key == cle)
        ).scalar_one_or_none()
        if deja is not None:
            report.duplicates += 1
            continue

        record_outreach_event(
            session,
            product_id=envoi.product_id,
            campaign_id=envoi.campaign_id,
            prospect_id=envoi.prospect_id,
            draft_id=envoi.draft_id,
            event_type=type_local,
            occurred_at=event.occurred_at,
            actor="sendgrid",
            # Un code, jamais la phrase du serveur distant : elle contient
            # l'adresse du destinataire.
            payload={
                "provider": "sendgrid",
                "reason_code": event.bounce_type or event.kind,
            },
            idempotency_key=cle,
            # C'est bien le fournisseur qui l'a constaté, pas quelqu'un qui
            # l'a saisi : c'est tout l'intérêt de brancher ce webhook.
            event_source=EventSource.OBSERVED,
        )
        report.accepted += 1
        code = event.bounce_type or event.kind
        report.reasons[code] = report.reasons.get(code, 0) + 1

        motif = _opposition_reason(event)
        if motif and _register_opposition(
            session, envoi=envoi, reason=motif, source=event.kind, now=moment
        ):
            report.oppositions += 1

    session.flush()
    return report


def _opposition_reason(event: ProviderEvent) -> str:
    """Le motif d'opposition que cet événement entraîne, s'il en entraîne un."""
    if event.kind in OPPOSITION_EVENTS:
        return OPPOSITION_EVENTS[event.kind]
    if event.kind == "bounce" and event.bounce_type == HARD_BOUNCE:
        return SuppressionReason.BOUNCE_HARD
    # `dropped` veut dire que SendGrid a refusé d'envoyer, généralement parce
    # qu'il tient déjà l'adresse pour perdue. L'événement est conservé pour
    # le diagnostic, mais il n'ajoute pas d'opposition de notre côté : nous
    # n'avons pas la raison, et inscrire une opposition sur une supposition
    # retirerait une organisation de la prospection sans qu'elle l'ait
    # demandé.
    return ""


def _register_opposition(
    session: Session,
    *,
    envoi: OutreachEvent,
    reason: str,
    source: str,
    now: datetime,
) -> bool:
    """Inscrit l'opposition, à partir du prospect et non de l'événement.

    L'adresse n'est pas lue dans la charge SendGrid : elle est relue chez
    nous. Une charge forgée pourrait sinon faire inscrire n'importe quelle
    adresse — et la signature protège l'expéditeur, pas le contenu d'un
    compte SendGrid compromis.
    """
    if envoi.prospect_id is None:
        return False
    prospect = session.get(ProspectCandidate, envoi.prospect_id)
    if prospect is None or not prospect.contact_email:
        return False

    add_suppression(
        session,
        value=prospect.contact_email,
        reason=reason,
        scope=SuppressionScope.GLOBAL,
        source=f"sendgrid:{source}",
        note="Constaté par le fournisseur d'expédition.",
        now=now,
    )
    return True
