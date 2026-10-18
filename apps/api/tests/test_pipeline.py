"""Dossiers commerciaux et prochaines actions.

Le parcours s'arrêtait au prospect : on savait qu'une organisation avait été
contactée, pas **qui devait faire quoi cette semaine**. Ces tests portent sur
la réponse à cette question, et sur les règles qui empêchent le suivi de
mentir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from growth_agent.agents.base import AgentContext
from growth_agent.models.enums import (
    LossReason,
    NextActionKind,
    NextActionStatus,
    OpportunityStage,
    OutreachEventType,
    ProspectStage,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.pipeline import NextAction
from growth_agent.services.pipeline import (
    DemoReport,
    PipelineError,
    advance,
    complete_action,
    open_opportunity,
    overdue_actions,
    record_demo,
    schedule_action,
    workload_by_owner,
)

AWA = "awa@techlearn-saem.com"
MERVEILLE = "merveille@techlearn-saem.com"


def _opportunity(session, product, make_prospect, **kwargs):
    prospect = kwargs.pop("prospect", None) or make_prospect(product=product)
    return open_opportunity(
        session,
        prospect=prospect,
        owner_email=kwargs.pop("owner_email", AWA),
        actor="test@exemple.example.org",
        **kwargs,
    )


# --- Ouvrir un dossier -----------------------------------------------------


def test_opening_a_file_copies_the_organisation_name(session, product_educore, make_prospect):
    """Le nom est dupliqué exprès : la purge anonymise le prospect, et le
    dossier doit rester lisible. Une raison sociale n'est pas une donnée
    personnelle."""
    prospect = make_prospect(product=product_educore, organization_name="Lycée de Douala")
    dossier = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    assert dossier.organization_name == "Lycée de Douala"
    assert dossier.stage == OpportunityStage.OPEN
    assert dossier.owner_email == AWA


def test_a_prospect_never_gets_two_open_files(session, product_educore, make_prospect):
    """Deux dossiers, ce sont deux personnes qui relancent sans le savoir."""
    prospect = make_prospect(product=product_educore)
    premier = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    second = _opportunity(
        session, product_educore, make_prospect, prospect=prospect, owner_email=MERVEILLE
    )
    assert second.id == premier.id
    assert second.owner_email == AWA, "le dossier existant ne doit pas changer de main"


def test_a_closed_file_does_not_block_a_new_one(session, product_educore, make_prospect):
    """Perdre un dossier n'interdit pas de recontacter l'organisation
    l'an prochain."""
    prospect = make_prospect(product=product_educore)
    premier = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    advance(
        session,
        opportunity=premier,
        stage=OpportunityStage.LOST,
        loss_reason=LossReason.TIMING,
        actor="test@exemple.example.org",
    )
    second = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    assert second.id != premier.id


# --- Faire avancer ---------------------------------------------------------


def test_losing_a_file_requires_a_reason(session, product_educore, make_prospect):
    """« Perdu » sans motif n'apprend rien et ne permet aucune correction."""
    dossier = _opportunity(session, product_educore, make_prospect)
    with pytest.raises(PipelineError, match="motif"):
        advance(
            session,
            opportunity=dossier,
            stage=OpportunityStage.LOST,
            actor="test@exemple.example.org",
        )


def test_an_unknown_loss_reason_is_refused(session, product_educore, make_prospect):
    """Liste fermée : un champ libre produit autant de motifs que de
    dossiers, et on ne peut plus rien additionner."""
    dossier = _opportunity(session, product_educore, make_prospect)
    with pytest.raises(PipelineError, match="inconnu"):
        advance(
            session,
            opportunity=dossier,
            stage=OpportunityStage.LOST,
            loss_reason="pas_envie",
            actor="test@exemple.example.org",
        )


def test_a_file_never_goes_backwards(session, product_educore, make_prospect):
    """Reculer fausserait les délais médians, qui comptent les premières
    fois."""
    dossier = _opportunity(session, product_educore, make_prospect)
    advance(
        session,
        opportunity=dossier,
        stage=OpportunityStage.PROPOSAL,
        actor="test@exemple.example.org",
    )
    with pytest.raises(PipelineError, match="recule"):
        advance(
            session,
            opportunity=dossier,
            stage=OpportunityStage.DEMO,
            actor="test@exemple.example.org",
        )


def test_a_closed_file_cannot_be_advanced(session, product_educore, make_prospect):
    dossier = _opportunity(session, product_educore, make_prospect)
    advance(
        session,
        opportunity=dossier,
        stage=OpportunityStage.WON,
        actor="test@exemple.example.org",
    )
    with pytest.raises(PipelineError, match="déjà clos"):
        advance(
            session,
            opportunity=dossier,
            stage=OpportunityStage.PILOT,
            actor="test@exemple.example.org",
        )


def test_advancing_records_a_funnel_event(session, product_educore, make_prospect):
    """Le tableau de bord se recalcule depuis les événements, jamais depuis
    l'état courant : un dossier gagné aujourd'hui ne réécrit pas hier."""
    dossier = _opportunity(session, product_educore, make_prospect)
    advance(
        session,
        opportunity=dossier,
        stage=OpportunityStage.WON,
        actor="test@exemple.example.org",
    )
    session.flush()
    types = [
        event.event_type
        for event in session.query(OutreachEvent)
        .filter(OutreachEvent.prospect_id == dossier.prospect_id)
        .all()
    ]
    assert OutreachEventType.DEAL_WON in types


def test_replaying_a_stage_does_not_inflate_the_funnel(session, product_educore, make_prospect):
    dossier = _opportunity(session, product_educore, make_prospect)
    advance(
        session,
        opportunity=dossier,
        stage=OpportunityStage.PILOT,
        actor="test@exemple.example.org",
    )
    # Le dossier est clos pour l'avancement, mais l'événement ne doit de
    # toute façon exister qu'une fois.
    session.flush()
    assert (
        session.query(OutreachEvent)
        .filter(OutreachEvent.event_type == OutreachEventType.PILOT_STARTED)
        .count()
        == 1
    )


def test_a_won_file_moves_the_prospect_too(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    dossier = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    advance(
        session,
        opportunity=dossier,
        stage=OpportunityStage.WON,
        actor="test@exemple.example.org",
    )
    assert prospect.stage == ProspectStage.WON


# --- Compte rendu de démonstration ----------------------------------------


def test_a_held_demo_advances_and_produces_a_next_step(session, product_educore, make_prospect):
    dossier = _opportunity(session, product_educore, make_prospect)
    action = record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=True, summary="Intéressés par les absences."),
        actor="test@exemple.example.org",
    )
    assert dossier.stage == OpportunityStage.DEMO
    assert action is not None
    assert action.kind == NextActionKind.PROPOSAL
    assert action.owner_email == AWA


def test_a_missed_demo_still_produces_a_next_step(session, product_educore, make_prospect):
    """Un rendez-vous manqué sans suite est un dossier qu'on abandonne sans
    le décider."""
    dossier = _opportunity(session, product_educore, make_prospect)
    action = record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=False),
        actor="test@exemple.example.org",
    )
    assert dossier.stage == OpportunityStage.OPEN
    assert action.kind == NextActionKind.DEMO
    assert "Reprogrammer" in action.title


def test_the_audit_trail_carries_categories_not_verbatims(session, product_educore, make_prospect):
    """Les objections sont des catégories. Le compte rendu, lui, raconte un
    échange avec une personne : il reste dans les notes du dossier, soumises
    à la purge, et n'entre pas dans le journal d'audit."""
    from growth_agent.models.ops import AuditEvent

    dossier = _opportunity(session, product_educore, make_prospect)
    verbatim = "Le directeur a dit qu'il trouvait le prix trop élevé."
    record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=True, summary=verbatim, objections=("no_budget",)),
        actor="test@exemple.example.org",
    )
    session.flush()

    entree = session.query(AuditEvent).filter(AuditEvent.action == "pipeline.demo_reported").one()
    assert entree.metadata_json["objections"] == ["no_budget"]
    assert "directeur" not in json.dumps(entree.metadata_json, ensure_ascii=False).lower()
    # Le texte existe bien quelque part — il n'a pas été perdu, il est juste
    # au bon endroit.
    assert verbatim in dossier.notes


def test_the_purge_reaches_the_new_tables(session, product_educore, make_prospect):
    """Les tables du pipeline portent du texte saisi à la main. Les oublier
    laisserait des données personnelles survivre à la durée de conservation
    du prospect auquel elles se rapportent."""
    from growth_agent.config import Settings
    from growth_agent.retention.service import PurgeService

    # Deux prospects, dont un seul échu : le garde-fou de volume refuse une
    # purge qui toucherait plus de la moitié d'une table, et il a raison.
    make_prospect(product=product_educore, domain="encore-valide.example.org")
    prospect = make_prospect(product=product_educore, domain="echu.example.org")
    prospect.retention_until = datetime.now(UTC) - timedelta(days=1)
    dossier = _opportunity(session, product_educore, make_prospect, prospect=prospect)
    record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=True, summary="Madame Ebongué doute du calendrier."),
        actor="test@exemple.example.org",
    )
    action = session.query(NextAction).filter(NextAction.prospect_id == prospect.id).one()
    action.outcome = "Rappelée sur son portable personnel."
    session.flush()

    PurgeService(session, settings=Settings(purge_enabled=True)).apply(dry_run=False)
    session.flush()
    session.refresh(dossier)
    session.refresh(action)

    assert "Ebongué" not in dossier.notes
    assert dossier.notes == "[anonymisé]"
    assert action.outcome == "[anonymisé]"
    assert action.title == "[anonymisé]"
    # La raison sociale reste : c'est un fait d'entreprise, et la retirer
    # viderait les indicateurs historiques.
    assert dossier.organization_name
    # Le porteur est un salarié, pas un prospect : autre régime.
    assert action.owner_email == AWA


# --- Prochaines actions ----------------------------------------------------


def test_an_action_without_an_owner_is_refused(session, product_educore, make_prospect):
    """Sans porteur, elle n'est faite par personne."""
    dossier = _opportunity(session, product_educore, make_prospect)
    with pytest.raises(PipelineError, match="porteur"):
        schedule_action(
            session,
            opportunity=dossier,
            kind=NextActionKind.CALL,
            title="Appeler",
            owner_email="   ",
            due_at=datetime.now(UTC) + timedelta(days=1),
            actor="test@exemple.example.org",
        )


def test_overdue_is_what_is_late_and_still_open(session, product_educore, make_prospect):
    dossier = _opportunity(session, product_educore, make_prospect)
    maintenant = datetime.now(UTC)
    en_retard = schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="En retard",
        owner_email=AWA,
        due_at=maintenant - timedelta(days=2),
        actor="test@exemple.example.org",
    )
    schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="À venir",
        owner_email=AWA,
        due_at=maintenant + timedelta(days=2),
        actor="test@exemple.example.org",
    )
    faite = schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="Faite mais tardive",
        owner_email=AWA,
        due_at=maintenant - timedelta(days=3),
        actor="test@exemple.example.org",
    )
    complete_action(session, action=faite, actor="test@exemple.example.org")

    retards = overdue_actions(session)
    assert [action.id for action in retards] == [en_retard.id]


def test_a_completed_action_cannot_be_completed_twice(session, product_educore, make_prospect):
    dossier = _opportunity(session, product_educore, make_prospect)
    action = schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="Appeler",
        owner_email=AWA,
        due_at=datetime.now(UTC),
        actor="test@exemple.example.org",
    )
    complete_action(session, action=action, actor="test@exemple.example.org")
    with pytest.raises(PipelineError, match="déjà close"):
        complete_action(session, action=action, actor="test@exemple.example.org")


def test_workload_shows_who_is_drowning(session, product_educore, make_prospect):
    """Un total global masque la personne qui croule."""
    dossier = _opportunity(session, product_educore, make_prospect)
    maintenant = datetime.now(UTC)
    for _ in range(3):
        schedule_action(
            session,
            opportunity=dossier,
            kind=NextActionKind.CALL,
            title="Retard",
            owner_email=AWA,
            due_at=maintenant - timedelta(days=1),
            actor="test@exemple.example.org",
        )
    schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="Plus tard",
        owner_email=MERVEILLE,
        due_at=maintenant + timedelta(days=5),
        actor="test@exemple.example.org",
    )
    charge = workload_by_owner(session)
    assert charge[0]["owner_email"] == AWA
    assert charge[0]["overdue"] == 3
    assert charge[1]["owner_email"] == MERVEILLE
    assert charge[1]["overdue"] == 0


# --- Les tâches CRM redeviennent visibles ---------------------------------


def test_a_coordinator_task_is_mirrored_locally(session, product_educore, make_prospect, crm, ctx):
    """Les tâches partaient dans le CRM et n'en revenaient jamais : le
    tableau de bord ne pouvait pas les voir."""
    from growth_agent.agents.sales_coordinator import SalesCoordinatorAgent

    prospect = make_prospect(product=product_educore)
    resultat = SalesCoordinatorAgent(crm).execute(ctx, prospect=prospect, owner_email=MERVEILLE)
    session.flush()

    assert resultat.value.next_action_id
    action = session.get(NextAction, resultat.value.next_action_id)
    assert action is not None
    assert action.owner_email == MERVEILLE
    assert action.status == NextActionStatus.OPEN
    # Et le lien vers la tâche distante est conservé.
    assert action.crm_remote_id == resultat.value.tasks_created[0]


def test_a_mirrored_task_appears_in_the_workload(session, product_educore, make_prospect, crm, ctx):
    from growth_agent.agents.sales_coordinator import SalesCoordinatorAgent

    SalesCoordinatorAgent(crm).execute(
        ctx, prospect=make_prospect(product=product_educore), owner_email=MERVEILLE
    )
    session.flush()
    charge = workload_by_owner(session)
    assert any(entree["owner_email"] == MERVEILLE for entree in charge)


def test_no_task_no_mirror_for_a_suppressed_contact(
    session, product_educore, make_prospect, crm, ctx
):
    """L'opposition prime : ni tâche CRM, ni double local."""
    from growth_agent.agents.sales_coordinator import SalesCoordinatorAgent
    from growth_agent.services.suppression import add_suppression

    prospect = make_prospect(product=product_educore, contact_email="non@exemple.example.org")
    add_suppression(session, value="non@exemple.example.org")
    session.flush()

    resultat = SalesCoordinatorAgent(crm).execute(ctx, prospect=prospect)
    assert resultat.value.tasks_created == []
    assert resultat.value.next_action_id == ""
    assert session.query(NextAction).count() == 0


# --- Le contexte d'agent, pour les tests ci-dessus ------------------------


@pytest.fixture
def ctx(session):
    return AgentContext(session=session, actor="test@exemple.example.org", actor_role="commercial")
