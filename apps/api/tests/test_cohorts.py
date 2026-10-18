"""Lecture par cohorte, délais médians, motifs de perte.

Un entonnoir en volumes cumulés mélange des organisations contactées il y a
six mois avec d'autres contactées hier. Le total monte, et on ne sait pas si
la conversion s'améliore. Ces tests portent sur ce qui rend la lecture
honnête : la cohorte de départ, la médiane plutôt que la moyenne, et
l'avertissement sur une cohorte trop jeune.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from growth_agent.models.enums import (
    DeliveryState,
    LossReason,
    NextActionKind,
    OpportunityStage,
    OutreachEventType,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.services.cohorts import MATURITY_DAYS, compute_cohorts
from growth_agent.services.pipeline import advance, open_opportunity, schedule_action

AWA = "awa@techlearn-saem.com"


def _event(session, product, prospect, event_type, when, *, simulated=False, payload=None):
    session.add(
        OutreachEvent(
            product_id=product.id,
            prospect_id=prospect.id,
            event_type=event_type,
            channel="email",
            occurred_at=when,
            actor="test",
            payload=payload or {},
            is_simulated=simulated,
            delivery_state=(DeliveryState.SENT if event_type == OutreachEventType.SENT else None),
        )
    )
    session.flush()


def _report(session, product, **kwargs):
    return compute_cohorts(session, product_id=product.id, product_code=product.code, **kwargs)


# --- La cohorte de départ --------------------------------------------------


def test_without_any_real_send_there_is_no_cohort(session, product_educore, make_prospect):
    """Les répétitions en simulation ne forment pas de cohorte."""
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        datetime.now(UTC) - timedelta(days=60),
        simulated=True,
    )
    rapport = _report(session, product_educore)
    assert rapport.cohorts == []
    assert any("simulation" in note for note in rapport.notes)


def test_a_prospect_belongs_to_the_month_of_its_first_contact(
    session, product_educore, make_prospect
):
    prospect = make_prospect(product=product_educore)
    depart = datetime(2026, 8, 12, tzinfo=UTC)
    _event(session, product_educore, prospect, OutreachEventType.SENT, depart)
    # Une relance plus tard ne change pas la cohorte d'origine.
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        depart + timedelta(days=40),
    )
    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert [ligne.cohort for ligne in rapport.cohorts] == ["2026-08"]
    assert rapport.cohorts[0].counts["contacted"] == 1


def test_conversion_is_read_against_the_cohort_not_the_total(
    session, product_educore, make_prospect
):
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    aout = datetime(2026, 8, 5, tzinfo=UTC)
    septembre = datetime(2026, 9, 5, tzinfo=UTC)

    # Août : 2 contactés, 2 réponses — la cohorte convertit bien.
    for index in range(2):
        prospect = make_prospect(product=product_educore, domain=f"aout{index}.example.org")
        _event(session, product_educore, prospect, OutreachEventType.SENT, aout)
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.REPLIED,
            aout + timedelta(days=2),
        )
    # Septembre : 4 contactés, 1 réponse — la cohorte convertit mal.
    for index in range(4):
        prospect = make_prospect(product=product_educore, domain=f"sept{index}.example.org")
        _event(session, product_educore, prospect, OutreachEventType.SENT, septembre)
        if index == 0:
            _event(
                session,
                product_educore,
                prospect,
                OutreachEventType.REPLIED,
                septembre + timedelta(days=3),
            )

    rapport = _report(session, product_educore, now=maintenant)
    par_cohorte = {ligne.cohort: ligne for ligne in rapport.cohorts}
    assert par_cohorte["2026-08"].conversion["replied"] == 1.0
    assert par_cohorte["2026-09"].conversion["replied"] == 0.25
    # En cumulé on lirait 3 réponses sur 6, soit 50 % — un chiffre que ni
    # l'une ni l'autre des deux cohortes n'a produit.


def test_a_young_cohort_is_flagged_not_hidden(session, product_educore, make_prospect):
    """Ses taux sont mécaniquement bas : les afficher sans avertir
    inviterait à conclure d'une baisse qui n'est qu'un manque de recul."""
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        maintenant - timedelta(days=3),
    )
    rapport = _report(session, product_educore, now=maintenant)
    ligne = rapport.cohorts[0]
    assert ligne.mature is False
    assert str(MATURITY_DAYS) in ligne.note
    assert any("30 jours" in note for note in rapport.notes)


def test_a_reservation_without_follow_through_is_not_a_contact(
    session, product_educore, make_prospect
):
    prospect = make_prospect(product=product_educore)
    session.add(
        OutreachEvent(
            product_id=product_educore.id,
            prospect_id=prospect.id,
            event_type=OutreachEventType.SENT,
            channel="email",
            occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
            actor="test",
            payload={},
            is_simulated=False,
            delivery_state=DeliveryState.UNCERTAIN,
        )
    )
    session.flush()
    assert _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC)).cohorts == []


# --- Délais médians --------------------------------------------------------


def test_the_median_resists_one_file_that_drags(session, product_educore, make_prospect):
    """Un dossier qui traîne six mois déplace une moyenne et ne dit rien du
    cas courant."""
    depart = datetime(2026, 8, 1, tzinfo=UTC)
    for index, delai in enumerate([2, 3, 4, 180]):
        prospect = make_prospect(product=product_educore, domain=f"d{index}.example.org")
        _event(session, product_educore, prospect, OutreachEventType.SENT, depart)
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.REPLIED,
            depart + timedelta(days=delai),
        )
    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    # Médiane de [2, 3, 4, 180] = 3,5. La moyenne serait 47,25.
    assert rapport.median_days["contacted_to_replied"] == 3.5


def test_a_stage_never_reached_has_no_median(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        datetime(2026, 8, 1, tzinfo=UTC),
    )
    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert rapport.median_days["contacted_to_replied"] is None


# --- Motifs de perte et pipeline ouvert -----------------------------------


def test_losses_are_counted_by_reason(session, product_educore, make_prospect):
    for index, motif in enumerate([LossReason.NO_BUDGET, LossReason.NO_BUDGET, LossReason.TIMING]):
        prospect = make_prospect(product=product_educore, domain=f"perdu{index}.example.org")
        dossier = open_opportunity(
            session, prospect=prospect, owner_email=AWA, actor="test@exemple.example.org"
        )
        advance(
            session,
            opportunity=dossier,
            stage=OpportunityStage.LOST,
            loss_reason=motif,
            actor="test@exemple.example.org",
        )
    rapport = _report(session, product_educore)
    assert rapport.loss_reasons[LossReason.NO_BUDGET] == 2
    assert rapport.loss_reasons[LossReason.TIMING] == 1


def test_blocked_sends_are_surfaced_by_reason(session, product_educore, make_prospect):
    """Déjà mesurés depuis le lot 3, jamais affichés jusqu'ici."""
    prospect = make_prospect(product=product_educore)
    for motif in ("suppressed", "suppressed", "stale_inbox"):
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.SEND_BLOCKED,
            datetime.now(UTC) - timedelta(days=1),
            payload={"reason_code": motif},
        )
    rapport = _report(session, product_educore)
    assert rapport.blocked_by_reason["suppressed"] == 2
    assert rapport.blocked_by_reason["stale_inbox"] == 1


def test_overdue_actions_are_attributed(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    dossier = open_opportunity(
        session, prospect=prospect, owner_email=AWA, actor="test@exemple.example.org"
    )
    schedule_action(
        session,
        opportunity=dossier,
        kind=NextActionKind.CALL,
        title="En retard",
        owner_email=AWA,
        due_at=datetime.now(UTC) - timedelta(days=2),
        actor="test@exemple.example.org",
    )
    rapport = _report(session, product_educore)
    assert rapport.overdue_by_owner == [{"owner_email": AWA, "overdue": 1}]
