"""Dossiers commerciaux et prochaines actions.

Le parcours s'arrêtait au prospect. Ce service le prolonge jusqu'à la
décision, et surtout il rend l'engagement **lisible** : une action a un
porteur et une échéance, et une action en retard se compte.

Deux règles de conception méritent d'être dites.

**Un dossier ouvert à la fois par organisation.** Deux dossiers sur le même
prospect, ce sont deux personnes qui la relancent sans le savoir. Ouvrir sur
un prospect qui en a déjà un renvoie l'existant plutôt que d'en créer un
second.

**Fermer un dossier exige un motif.** « Perdu » sans raison n'apprend rien et
ne permet aucune correction : les motifs sont une liste fermée, pour qu'on
puisse les additionner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models.catalog import Product
from growth_agent.models.enums import (
    EventSource,
    LossReason,
    NextActionKind,
    NextActionStatus,
    OpportunityStage,
    OutreachEventType,
    ProspectStage,
)
from growth_agent.models.pipeline import NextAction, Opportunity
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.services.audit import record_audit
from growth_agent.services.outreach import record_outreach_event
from growth_agent.services.suppression import check_suppression

#: Natures d'action qui s'adressent au prospect. Le contrôle d'opposition
#: porte sur elles, et sur elles seules : ranger un dossier ou écrire un
#: compte rendu ne dérange personne.
#:
#: Le blocage à l'envoi ne suffisait pas. Un rendez-vous manqué produisait
#: « Reprogrammer la démonstration », confié à une personne, pour un contact
#: qui venait d'écrire STOP. Le serveur n'envoyait rien ; l'appel téléphonique
#: partait quand même, et l'opposition était contournée par le seul canal
#: qu'aucun garde-fou technique ne surveille.
CONTACT_KINDS: frozenset[str] = frozenset(
    {
        NextActionKind.FOLLOW_UP,
        NextActionKind.CALL,
        NextActionKind.DEMO,
        NextActionKind.PROPOSAL,
        NextActionKind.RENEWAL,
    }
)

#: Étapes depuis lesquelles un dossier peut encore avancer.
OPEN_STAGES: frozenset[str] = frozenset(
    {
        OpportunityStage.OPEN,
        OpportunityStage.DEMO,
        OpportunityStage.PROPOSAL,
        OpportunityStage.PILOT,
    }
)

#: Ordre d'avancement. Un dossier ne recule pas : constater qu'on est revenu
#: en arrière fausserait les délais médians, qui comptent les premières fois.
STAGE_ORDER: tuple[str, ...] = (
    OpportunityStage.OPEN,
    OpportunityStage.DEMO,
    OpportunityStage.PROPOSAL,
    OpportunityStage.PILOT,
)

#: Événements de funnel déclenchés par un passage d'étape. Le tableau de bord
#: se recalcule depuis les événements, jamais depuis l'état courant : un
#: dossier gagné aujourd'hui ne doit pas réécrire l'histoire d'hier.
STAGE_EVENTS: dict[str, str] = {
    OpportunityStage.DEMO: OutreachEventType.DEMO_HELD,
    OpportunityStage.PILOT: OutreachEventType.PILOT_STARTED,
    OpportunityStage.WON: OutreachEventType.DEAL_WON,
    OpportunityStage.LOST: OutreachEventType.DEAL_LOST,
}

#: Étape du prospect correspondant à l'avancement du dossier.
PROSPECT_STAGE_FOR: dict[str, str] = {
    OpportunityStage.PILOT: ProspectStage.PILOT,
    OpportunityStage.WON: ProspectStage.WON,
    OpportunityStage.LOST: ProspectStage.LOST,
}


class PipelineError(Exception):
    """Opération refusée par une règle du pipeline."""


@dataclass(slots=True)
class DemoReport:
    """Compte rendu d'une démonstration tenue."""

    held: bool
    summary: str = ""
    objections: tuple[str, ...] = ()
    next_step: str = ""


def open_opportunity(
    session: Session,
    *,
    prospect: ProspectCandidate,
    owner_email: str,
    actor: str,
    actor_role: str = "",
    amount: int | None = None,
    expected_close_on: datetime | None = None,
    now: datetime | None = None,
) -> Opportunity:
    """Ouvre un dossier sur un prospect, ou renvoie celui qui existe déjà."""
    moment = now or datetime.now(UTC)
    existing = session.execute(
        select(Opportunity).where(
            Opportunity.prospect_id == prospect.id,
            Opportunity.stage.in_(OPEN_STAGES),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    opportunity = Opportunity(
        product_id=prospect.product_id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        organization_name=prospect.organization_name,
        stage=OpportunityStage.OPEN,
        owner_email=owner_email,
        amount=amount,
        currency=_currency_for(session, prospect.product_id),
        expected_close_on=expected_close_on,
    )
    session.add(opportunity)
    session.flush()

    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="pipeline.opportunity_opened",
        object_type="opportunity",
        object_id=opportunity.id,
        metadata={"owner": owner_email, "stage": opportunity.stage},
        now=moment,
    )
    return opportunity


def advance(
    session: Session,
    *,
    opportunity: Opportunity,
    stage: str,
    actor: str,
    actor_role: str = "",
    loss_reason: str = "",
    note: str = "",
    now: datetime | None = None,
) -> Opportunity:
    """Fait avancer un dossier, et enregistre l'événement correspondant."""
    moment = now or datetime.now(UTC)

    if opportunity.is_closed:
        raise PipelineError(
            f"Dossier déjà clos ({opportunity.stage}) : rouvrir exige un nouveau dossier."
        )
    if stage == OpportunityStage.LOST and not loss_reason:
        raise PipelineError("Un dossier perdu exige un motif : sans lui, « perdu » n'apprend rien.")
    if loss_reason and loss_reason not in set(LossReason):
        raise PipelineError(f"Motif de perte inconnu : {loss_reason!r}.")
    if stage in STAGE_ORDER and opportunity.stage in STAGE_ORDER:
        if STAGE_ORDER.index(stage) < STAGE_ORDER.index(opportunity.stage):
            raise PipelineError(f"Un dossier ne recule pas : {opportunity.stage} → {stage} refusé.")

    opportunity.stage = stage
    if note:
        opportunity.notes = f"{opportunity.notes}\n{note}".strip()
    if stage in {OpportunityStage.WON, OpportunityStage.LOST}:
        opportunity.closed_at = moment
        opportunity.loss_reason = loss_reason if stage == OpportunityStage.LOST else ""

    event_type = STAGE_EVENTS.get(stage)
    if event_type and opportunity.prospect_id:
        record_outreach_event(
            session,
            product_id=opportunity.product_id,
            campaign_id=opportunity.campaign_id,
            prospect_id=opportunity.prospect_id,
            event_type=event_type,
            occurred_at=moment,
            actor=actor,
            payload={"opportunity_id": str(opportunity.id), "loss_reason": loss_reason},
            # Une étape franchie est toujours saisie par quelqu'un : le
            # système ne constate ni une démonstration ni une signature.
            event_source=EventSource.DECLARED,
            # Un passage d'étape ne se produit qu'une fois par dossier :
            # rejouer l'appel ne doit pas gonfler les indicateurs.
            idempotency_key=f"stage:{opportunity.id}:{stage}",
        )

    prospect_stage = PROSPECT_STAGE_FOR.get(stage)
    if prospect_stage and opportunity.prospect_id:
        prospect = session.get(ProspectCandidate, opportunity.prospect_id)
        if prospect is not None:
            prospect.stage = prospect_stage

    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action=f"pipeline.opportunity_{stage}",
        object_type="opportunity",
        object_id=opportunity.id,
        metadata={"stage": stage, "loss_reason": loss_reason},
        now=moment,
    )
    session.flush()
    return opportunity


def record_demo(
    session: Session,
    *,
    opportunity: Opportunity,
    report: DemoReport,
    actor: str,
    actor_role: str = "",
    follow_up_in_days: int = 3,
    now: datetime | None = None,
) -> NextAction | None:
    """Enregistre le compte rendu d'une démonstration.

    Une démonstration sans compte rendu est une démonstration qu'on ne saura
    pas exploiter : ni ce qui a été objecté, ni ce qu'on a promis de faire.
    Le compte rendu produit donc **toujours** une prochaine action — même un
    rendez-vous manqué en appelle une.
    """
    moment = now or datetime.now(UTC)

    if report.held:
        advance(
            session,
            opportunity=opportunity,
            stage=OpportunityStage.DEMO,
            actor=actor,
            actor_role=actor_role,
            note=report.summary,
            now=moment,
        )
    elif opportunity.prospect_id:
        # Un créneau perdu n'est ni une démonstration tenue, ni une absence
        # de démonstration. Sans événement, il disparaît des chiffres, et on
        # ne saura jamais combien de rendez-vous ne se tiennent pas.
        record_outreach_event(
            session,
            product_id=opportunity.product_id,
            campaign_id=opportunity.campaign_id,
            prospect_id=opportunity.prospect_id,
            event_type=OutreachEventType.DEMO_NO_SHOW,
            occurred_at=moment,
            actor=actor,
            payload={"opportunity_id": str(opportunity.id)},
            event_source=EventSource.DECLARED,
        )
    # Un rendez-vous manqué appelle une suite, mais laquelle dépend de ce que
    # le contact a dit entre-temps. S'il s'est opposé, la seule suite
    # admissible est de ranger le dossier : reprogrammer reviendrait à le
    # rappeler, et le téléphone ne connaît pas la liste d'opposition.
    prospect = (
        session.get(ProspectCandidate, opportunity.prospect_id) if opportunity.prospect_id else None
    )
    verdict = check_suppression(
        session,
        email=prospect.contact_email if prospect else None,
        product_id=opportunity.product_id,
        now=moment,
    )
    oppose = verdict.suppressed and verdict.reason != "invalid_recipient"

    if oppose:
        kind = NextActionKind.CLOSE_OUT
        titre = f"Clôturer le dossier — opposition enregistrée ({opportunity.organization_name})"
    else:
        kind = NextActionKind.PROPOSAL if report.held else NextActionKind.DEMO
        titre = report.next_step or (
            f"Suite de la démonstration — {opportunity.organization_name}"
            if report.held
            else f"Reprogrammer la démonstration — {opportunity.organization_name}"
        )
    action = schedule_action(
        session,
        opportunity=opportunity,
        kind=kind,
        title=titre,
        owner_email=opportunity.owner_email,
        due_at=moment + timedelta(days=max(1, follow_up_in_days)),
        actor=actor,
        actor_role=actor_role,
        now=moment,
    )
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="pipeline.demo_reported",
        object_type="opportunity",
        object_id=opportunity.id,
        metadata={
            "held": report.held,
            "opposition": oppose,
            # Les objections sont des catégories, pas des verbatims : le
            # journal d'audit ne doit porter aucun propos de personne.
            "objections": list(report.objections),
        },
        now=moment,
    )
    session.flush()
    return action


def schedule_action(
    session: Session,
    *,
    opportunity: Opportunity | None = None,
    product_id: uuid.UUID | None = None,
    prospect_id: uuid.UUID | None = None,
    kind: str,
    title: str,
    owner_email: str,
    due_at: datetime,
    actor: str,
    actor_role: str = "",
    crm_remote_id: str = "",
    now: datetime | None = None,
) -> NextAction:
    """Inscrit une action à faire. Sans porteur, elle n'est faite par personne."""
    moment = now or datetime.now(UTC)
    resolved_product = product_id or (opportunity.product_id if opportunity else None)
    if resolved_product is None:
        raise PipelineError("Une action doit porter sur un produit.")
    if not owner_email.strip():
        raise PipelineError(
            "Une action sans porteur n'est faite par personne : indiquer un responsable."
        )

    resolved_prospect = prospect_id or (opportunity.prospect_id if opportunity else None)
    if kind in CONTACT_KINDS and resolved_prospect is not None:
        prospect = session.get(ProspectCandidate, resolved_prospect)
        verdict = check_suppression(
            session,
            email=prospect.contact_email if prospect else None,
            product_id=resolved_product,
            now=moment,
        )
        # Seule une opposition arrête l'action. Un prospect sans adresse n'a
        # rien refusé : l'appeler reste possible, et le lui interdire ici
        # bloquerait un travail légitime au nom d'un refus inexistant.
        if verdict.suppressed and verdict.reason != "invalid_recipient":
            raise PipelineError(
                "Opposition enregistrée pour ce contact : aucune relance ne peut "
                "lui être adressée, y compris par téléphone. Clôturer le dossier."
            )

    action = NextAction(
        opportunity_id=opportunity.id if opportunity else None,
        product_id=resolved_product,
        prospect_id=resolved_prospect,
        kind=kind,
        title=title.strip(),
        owner_email=owner_email.strip().lower(),
        due_at=due_at,
        status=NextActionStatus.OPEN,
        crm_remote_id=crm_remote_id,
    )
    session.add(action)
    session.flush()

    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action="pipeline.action_scheduled",
        object_type="next_action",
        object_id=action.id,
        metadata={"kind": kind, "owner": action.owner_email, "due_at": due_at.isoformat()},
        now=moment,
    )
    return action


def complete_action(
    session: Session,
    *,
    action: NextAction,
    actor: str,
    actor_role: str = "",
    outcome: str = "",
    cancelled: bool = False,
    now: datetime | None = None,
) -> NextAction:
    moment = now or datetime.now(UTC)
    if action.status != NextActionStatus.OPEN:
        raise PipelineError(f"Action déjà close ({action.status}).")

    action.status = NextActionStatus.CANCELLED if cancelled else NextActionStatus.DONE
    action.completed_at = moment
    action.outcome = outcome
    record_audit(
        session,
        actor=actor,
        actor_role=actor_role,
        action=f"pipeline.action_{action.status}",
        object_type="next_action",
        object_id=action.id,
        metadata={"kind": action.kind},
        now=moment,
    )
    session.flush()
    return action


def overdue_actions(
    session: Session,
    *,
    product_id: uuid.UUID | None = None,
    owner_email: str = "",
    now: datetime | None = None,
    limit: int = 200,
) -> list[NextAction]:
    """Ce qui est en retard. La question du lundi matin."""
    moment = now or datetime.now(UTC)
    statement = (
        select(NextAction)
        .where(NextAction.status == NextActionStatus.OPEN, NextAction.due_at < moment)
        .order_by(NextAction.due_at)
        .limit(min(limit, 500))
    )
    if product_id is not None:
        statement = statement.where(NextAction.product_id == product_id)
    if owner_email:
        statement = statement.where(NextAction.owner_email == owner_email.strip().lower())
    return list(session.execute(statement).scalars())


def workload_by_owner(
    session: Session,
    *,
    product_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Charge par personne : ce qui est en retard, aujourd'hui, et à venir.

    Agrégé par porteur parce que c'est ainsi qu'on répartit le travail —
    et parce qu'un total global masque la personne qui croule.
    """
    moment = now or datetime.now(UTC)
    fin_de_journee = moment + timedelta(days=1)

    statement = select(NextAction).where(NextAction.status == NextActionStatus.OPEN)
    if product_id is not None:
        statement = statement.where(NextAction.product_id == product_id)

    par_porteur: dict[str, dict[str, object]] = {}
    for action in session.execute(statement).scalars():
        entree = par_porteur.setdefault(
            action.owner_email,
            {"owner_email": action.owner_email, "overdue": 0, "today": 0, "upcoming": 0},
        )
        if action.due_at < moment:
            entree["overdue"] = int(entree["overdue"]) + 1
        elif action.due_at < fin_de_journee:
            entree["today"] = int(entree["today"]) + 1
        else:
            entree["upcoming"] = int(entree["upcoming"]) + 1

    return sorted(
        par_porteur.values(),
        key=lambda entree: (-int(entree["overdue"]), str(entree["owner_email"])),
    )


def _currency_for(session: Session, product_id: uuid.UUID) -> str:
    """Devise du produit. XAF au Cameroun, EUR en France — le catalogue
    tranche, pas le code."""
    product = session.get(Product, product_id)
    return getattr(product, "currency", "") or "XAF"
