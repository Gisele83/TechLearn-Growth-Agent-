"""Historique de démonstration pour les captures d'écran.

Écrit des lignes en base, uniquement. Aucun envoi : le mode d'envoi réel
reste désactivé, et ces événements ne sont pas marqués `is_simulated` parce
qu'ils représentent ce qu'aurait produit une campagne réelle — c'est la
seule façon de montrer l'écran avec des cohortes.
"""

from __future__ import annotations

import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from growth_agent.db.session import session_scope
from growth_agent.models.catalog import Campaign, Product
from growth_agent.models.enums import (
    EventSource,
    LossReason,
    NextActionKind,
    NextActionStatus,
    OpportunityStage,
    OutreachEventType,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.pipeline import NextAction, Opportunity
from growth_agent.models.prospect import ProspectCandidate, ProspectSource

MAINTENANT = datetime.now(UTC)
PORTEURS = ["awa.mbarga@techlearn-saem.com", "merveille.nguetse@techlearn-saem.com"]

# (mois en arrière, contactés, réponses, RDV, démos, pilotes, gains)
PLAN = [
    (3, 24, 9, 6, 5, 2, 1),
    (2, 30, 14, 9, 7, 3, 2),
    (1, 28, 8, 4, 3, 1, 0),
    (0, 18, 3, 1, 0, 0, 0),
]


def debut_du_mois(recul: int) -> datetime:
    annee, mois = MAINTENANT.year, MAINTENANT.month - recul
    while mois <= 0:
        mois += 12
        annee -= 1
    return datetime(annee, mois, 6, 9, 0, tzinfo=UTC)


def evenement(session, produit, campagne, prospect, type_, quand, source, payload=None):
    session.add(
        OutreachEvent(
            product_id=produit.id,
            campaign_id=campagne.id if campagne else None,
            prospect_id=prospect.id,
            event_type=type_,
            channel="email",
            occurred_at=quand,
            actor="demo",
            payload=payload or {},
            is_simulated=False,
            event_source=source,
            delivery_state="sent" if type_ == OutreachEventType.SENT else None,
        )
    )


def main() -> int:
    alea = random.Random(20261018)
    with session_scope() as session:
        produit = session.execute(
            select(Product).where(Product.code == "educore")
        ).scalar_one()
        campagne = session.execute(
            select(Campaign).where(Campaign.product_id == produit.id)
        ).scalars().first()

        index = 0
        for recul, contactes, reponses, rdv, demos, pilotes, gains in PLAN:
            depart = debut_du_mois(recul)
            for rang in range(contactes):
                index += 1
                domaine = f"etab-{index:03d}.example.org"
                prospect = ProspectCandidate(
                    product_id=produit.id,
                    campaign_id=campagne.id if campagne else None,
                    organization_name=f"Établissement {index:03d}",
                    dedupe_key=f"domain:{domaine}",
                    website=f"https://{domaine}",
                    domain=domaine,
                    country_code="CM",
                    sector="Enseignement secondaire",
                    headcount_estimate=120,
                    contact_full_name=f"Contact {index:03d}",
                    contact_role="Directeur administratif",
                    contact_email=f"direction@{domaine}",
                )
                session.add(prospect)
                session.flush()
                session.add(
                    ProspectSource(
                        prospect_id=prospect.id,
                        kind="public_website",
                        url=f"https://{domaine}/a-propos",
                        title="À propos",
                        excerpt="Établissement en croissance.",
                        collected_at=depart - timedelta(days=5),
                        provider="demo",
                    )
                )
                envoi = depart + timedelta(days=alea.randint(0, 12), hours=alea.randint(0, 8))
                evenement(
                    session, produit, campagne, prospect,
                    OutreachEventType.SENT, envoi, EventSource.OBSERVED,
                    {"provider": "sendgrid", "simulated": False},
                )
                if rang < reponses:
                    repondu = envoi + timedelta(days=alea.randint(1, 9))
                    evenement(
                        session, produit, campagne, prospect,
                        OutreachEventType.REPLIED, repondu, EventSource.DECLARED,
                        {"intent": "interested", "confidence": 0.8},
                    )
                    if rang < rdv:
                        planifie = repondu + timedelta(days=alea.randint(2, 10))
                        evenement(
                            session, produit, campagne, prospect,
                            OutreachEventType.DEMO_SCHEDULED, planifie, EventSource.DECLARED,
                        )
                        dossier = Opportunity(
                            product_id=produit.id,
                            campaign_id=campagne.id if campagne else None,
                            prospect_id=prospect.id,
                            organization_name=prospect.organization_name,
                            stage=OpportunityStage.OPEN,
                            owner_email=PORTEURS[index % len(PORTEURS)],
                            currency="XAF",
                        )
                        session.add(dossier)
                        session.flush()
                        if rang < demos:
                            tenue = planifie + timedelta(days=alea.randint(1, 6))
                            evenement(
                                session, produit, campagne, prospect,
                                OutreachEventType.DEMO_HELD, tenue, EventSource.DECLARED,
                            )
                            dossier.stage = OpportunityStage.DEMO
                            if rang < pilotes:
                                demarre = tenue + timedelta(days=alea.randint(5, 20))
                                evenement(
                                    session, produit, campagne, prospect,
                                    OutreachEventType.PILOT_STARTED, demarre, EventSource.DECLARED,
                                )
                                dossier.stage = OpportunityStage.PILOT
                                if rang < gains:
                                    gagne = demarre + timedelta(days=alea.randint(5, 25))
                                    evenement(
                                        session, produit, campagne, prospect,
                                        OutreachEventType.DEAL_WON, gagne, EventSource.DECLARED,
                                    )
                                    dossier.stage = OpportunityStage.WON
                                    dossier.closed_at = gagne
                        else:
                            # Créneau perdu : ni démonstration tenue, ni absence.
                            evenement(
                                session, produit, campagne, prospect,
                                OutreachEventType.DEMO_NO_SHOW,
                                planifie + timedelta(days=1), EventSource.DECLARED,
                            )
                        # Quelques dossiers perdus, avec et sans motif.
                        if rang % 3 == 1 and dossier.stage != OpportunityStage.WON:
                            dossier.stage = OpportunityStage.LOST
                            dossier.closed_at = planifie + timedelta(days=20)
                            # Un dossier sur six est fermé sans motif : c'est
                            # ce que l'écran doit rendre visible.
                            dossier.loss_reason = (
                                ""
                                if rang % 6 == 1
                                else [
                                    LossReason.NO_BUDGET,
                                    LossReason.TIMING,
                                    LossReason.COMPETITOR,
                                    LossReason.NOT_DECISION_MAKER,
                                ][(rang + recul) % 4]
                            )
                        # Une action en retard sur certains dossiers ouverts.
                        if rang % 5 == 0 and dossier.stage in {
                            OpportunityStage.OPEN, OpportunityStage.DEMO
                        }:
                            session.add(
                                NextAction(
                                    opportunity_id=dossier.id,
                                    product_id=produit.id,
                                    prospect_id=prospect.id,
                                    kind=NextActionKind.FOLLOW_UP,
                                    title="Relancer après la démonstration",
                                    owner_email=dossier.owner_email,
                                    due_at=MAINTENANT - timedelta(days=alea.randint(1, 14)),
                                    status=NextActionStatus.OPEN,
                                )
                            )
                elif rang % 11 == 2:
                    evenement(
                        session, produit, campagne, prospect,
                        OutreachEventType.OPTED_OUT,
                        envoi + timedelta(days=2), EventSource.DECLARED,
                    )
            # Des envois bloqués, par motif.
            for motif, combien in (("suppressed", 3), ("frequency_contact", 2), ("stale_inbox", 1)):
                for _ in range(combien):
                    session.add(
                        OutreachEvent(
                            product_id=produit.id,
                            campaign_id=campagne.id if campagne else None,
                            event_type=OutreachEventType.SEND_BLOCKED,
                            channel="email",
                            occurred_at=depart + timedelta(days=4),
                            actor="demo",
                            payload={"reason_code": motif},
                            is_simulated=False,
                            event_source=EventSource.OBSERVED,
                            idempotency_key=f"blocked:{uuid.uuid4()}",
                        )
                    )
            session.flush()
        print("Historique de démonstration écrit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
