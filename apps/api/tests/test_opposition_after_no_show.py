"""Une opposition arrête aussi les relances humaines.

Le service d'envoi bloque un message adressé à un contact opposé : ce
contrôle existe depuis le lot 3 et il tient. Mais le pipeline, lui,
fabriquait des tâches pour des personnes. Un rendez-vous manqué produisait
« Reprogrammer la démonstration — X », confiée à un commercial, sans
regarder si X venait d'écrire STOP.

Le serveur n'envoyait rien. Le commercial décrochait son téléphone. La
machine était en règle et la personne était rappelée quand même : le seul
canal qu'aucun garde-fou technique ne surveille.

Ces tests décrivent le trou tel qu'il était, et ce qui le bouche.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from growth_agent.models.enums import (
    NextActionKind,
    NextActionStatus,
    OutreachEventType,
    SuppressionReason,
    SuppressionScope,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.services.pipeline import (
    CONTACT_KINDS,
    DemoReport,
    PipelineError,
    open_opportunity,
    record_demo,
    schedule_action,
)
from growth_agent.services.suppression import add_suppression, check_suppression

ACTEUR = "merveille@techlearn-saem.com"
PORTEUR = "awa@techlearn-saem.com"


def _oppose(session, prospect, *, scope=SuppressionScope.GLOBAL, product=None):
    return add_suppression(
        session,
        value=prospect.contact_email or "",
        reason=SuppressionReason.OPT_OUT,
        scope=scope,
        product_id=product.id if product else None,
        source="test",
        note="Opposition exprimée par le contact.",
    )


def _dossier(session, prospect):
    return open_opportunity(session, prospect=prospect, owner_email=PORTEUR, actor=ACTEUR)


# --- Le rendez-vous manqué -------------------------------------------------


def test_a_missed_meeting_after_an_opposition_does_not_schedule_a_call_back(
    session, product_educore, make_prospect
):
    """Le cas exact du trou : le compte rendu produisait « Reprogrammer »."""
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect)

    action = record_demo(session, opportunity=dossier, report=DemoReport(held=False), actor=ACTEUR)

    assert action.kind == NextActionKind.CLOSE_OUT
    assert action.kind not in CONTACT_KINDS
    assert "Reprogrammer" not in action.title
    assert "opposition" in action.title.lower()


def test_without_an_opposition_a_missed_meeting_still_asks_to_reschedule(
    session, product_educore, make_prospect
):
    """Le garde-fou ne doit pas éteindre le travail commercial ordinaire.

    Un contrôle qui bloque tout est aussi inutile qu'un contrôle absent :
    on le désactive au bout d'une semaine.
    """
    prospect = make_prospect(product=product_educore)
    action = record_demo(
        session,
        opportunity=_dossier(session, prospect),
        report=DemoReport(held=False),
        actor=ACTEUR,
    )
    assert action.kind == NextActionKind.DEMO
    assert "Reprogrammer" in action.title


def test_a_missed_meeting_is_still_recorded_when_the_contact_opposed(
    session, product_educore, make_prospect
):
    """L'opposition change la suite, pas les faits.

    Effacer le rendez-vous manqué ferait disparaître des chiffres un créneau
    réellement perdu — et l'on conclurait que les créneaux ne se perdent pas.
    """
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect)
    record_demo(session, opportunity=dossier, report=DemoReport(held=False), actor=ACTEUR)

    manques = (
        session.query(OutreachEvent)
        .filter(OutreachEvent.event_type == OutreachEventType.DEMO_NO_SHOW)
        .all()
    )
    assert len(manques) == 1


def test_the_close_out_action_is_open_and_attributed(session, product_educore, make_prospect):
    """Ranger un dossier est un travail : sans porteur ni échéance, il
    n'est fait par personne et le dossier reste ouvert indéfiniment."""
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect)
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    action = record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=False),
        actor=ACTEUR,
        now=maintenant,
    )
    assert action.status == NextActionStatus.OPEN
    assert action.owner_email == PORTEUR
    assert action.due_at > maintenant


# --- Toutes les relances, pas seulement celle-là ---------------------------


@pytest.mark.parametrize("kind", sorted(CONTACT_KINDS))
def test_no_contact_action_can_be_scheduled_for_an_opposed_contact(
    session, product_educore, make_prospect, kind
):
    """Boucher un trou par le haut, pas l'instance par l'instance.

    Corriger seulement `record_demo` laisserait passer le prochain appel à
    `schedule_action` écrit ailleurs. Le refus est donc posé là où toutes
    les actions naissent.
    """
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect)

    with pytest.raises(PipelineError, match="[Oo]pposition"):
        schedule_action(
            session,
            opportunity=dossier,
            kind=kind,
            title="Relancer",
            owner_email=PORTEUR,
            due_at=datetime.now(UTC) + timedelta(days=2),
            actor=ACTEUR,
        )


@pytest.mark.parametrize("kind", [NextActionKind.CLOSE_OUT, NextActionKind.DEMO_REPORT])
def test_internal_work_remains_possible_after_an_opposition(
    session, product_educore, make_prospect, kind
):
    """Rédiger un compte rendu ou ranger le dossier ne dérange personne.

    Les interdire enfermerait le dossier d'un contact opposé dans un état
    dont plus aucune action ne permet de sortir.
    """
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect)

    action = schedule_action(
        session,
        opportunity=dossier,
        kind=kind,
        title="Travail interne",
        owner_email=PORTEUR,
        due_at=datetime.now(UTC) + timedelta(days=2),
        actor=ACTEUR,
    )
    assert action.status == NextActionStatus.OPEN


def test_an_opposition_on_the_domain_stops_the_follow_up_too(
    session, product_educore, make_prospect
):
    """Une opposition posée sur un domaine couvre ses adresses.

    Ne contrôler que l'adresse exacte laisserait relancer la directrice
    après que l'établissement a demandé à ne plus être démarché.
    """
    prospect = make_prospect(
        product=product_educore,
        domain="lycee-oppose.example.org",
        contact_email="direction@lycee-oppose.example.org",
    )
    dossier = _dossier(session, prospect)
    add_suppression(
        session,
        value="lycee-oppose.example.org",
        reason=SuppressionReason.OPT_OUT,
        scope=SuppressionScope.GLOBAL,
        source="test",
    )
    assert check_suppression(session, email=prospect.contact_email).suppressed

    with pytest.raises(PipelineError, match="[Oo]pposition"):
        schedule_action(
            session,
            opportunity=dossier,
            kind=NextActionKind.CALL,
            title="Rappeler",
            owner_email=PORTEUR,
            due_at=datetime.now(UTC) + timedelta(days=1),
            actor=ACTEUR,
        )


def test_an_opposition_on_the_other_product_does_not_block_this_one(
    session, product_educore, product_maintrix, make_prospect
):
    """Une opposition de portée produit vaut pour ce produit.

    L'étendre à l'autre produit paraîtrait prudent et serait faux : la
    personne a refusé une sollicitation, pas toutes. Le refus global existe
    et s'écrit autrement.
    """
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    _oppose(session, prospect, scope=SuppressionScope.PRODUCT, product=product_maintrix)

    action = schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="Rappeler",
        owner_email=PORTEUR,
        due_at=datetime.now(UTC) + timedelta(days=1),
        actor=ACTEUR,
    )
    assert action.kind == NextActionKind.CALL


def test_a_prospect_without_an_address_refused_nothing(session, product_educore, make_prospect):
    """Sans adresse, `check_suppression` répond « bloqué » pour protéger
    l'envoi. Transposé tel quel ici, ce refus interdirait d'appeler une
    organisation qui n'a jamais rien demandé."""
    prospect = make_prospect(product=product_educore, contact_email=None)
    action = schedule_action(
        session,
        opportunity=_dossier(session, prospect),
        kind=NextActionKind.CALL,
        title="Rappeler le standard",
        owner_email=PORTEUR,
        due_at=datetime.now(UTC) + timedelta(days=1),
        actor=ACTEUR,
    )
    assert action.kind == NextActionKind.CALL


def test_an_opposition_recorded_after_the_action_does_not_rewrite_it(
    session, product_educore, make_prospect
):
    """Ce que le garde-fou ne fait pas, et qu'il faut dire.

    Une action inscrite avant l'opposition reste ouverte : elle a été
    décidée alors que rien ne s'y opposait. Son porteur doit l'annuler à la
    main. C'est une limite assumée, pas un oubli — et elle justifie que la
    page de suivi affiche les oppositions.
    """
    prospect = make_prospect(product=product_educore)
    dossier = _dossier(session, prospect)
    action = schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="Rappeler",
        owner_email=PORTEUR,
        due_at=datetime.now(UTC) + timedelta(days=5),
        actor=ACTEUR,
    )
    _oppose(session, prospect)
    session.refresh(action)
    assert action.status == NextActionStatus.OPEN
