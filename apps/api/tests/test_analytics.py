"""Indicateurs : reproductibles, datés, honnêtes sur l'absence de données."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from growth_agent.models.enums import OutreachEventType
from growth_agent.services.analytics import (
    compute_all_kpis,
    compute_product_kpis,
    funnel_timeseries,
)
from growth_agent.services.outreach import record_outreach_event


def _event(session, product, prospect, event_type, *, days_ago=0):
    return record_outreach_event(
        session,
        product_id=product.id,
        prospect_id=prospect.id,
        event_type=event_type,
        occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
        idempotency_key=f"{event_type}:{prospect.id}:{days_ago}",
    )


def test_kpis_are_zero_and_rates_null_on_an_empty_base(session, product_educore):
    kpis = compute_product_kpis(session, product_id=product_educore.id, product_code="educore")
    assert kpis.funnel.contacted == 0
    assert kpis.reply_rate is None
    assert kpis.demo_rate is None
    assert any("ne sont pas calculables" in note for note in kpis.notes)


def test_rates_are_computed_once_there_is_a_basis(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(session, product_educore, prospect, OutreachEventType.SENT)
    _event(session, product_educore, prospect, OutreachEventType.REPLIED)
    session.flush()

    kpis = compute_product_kpis(session, product_id=product_educore.id, product_code="educore")
    assert kpis.funnel.contacted == 1
    assert kpis.reply_rate == 1.0


def test_repeated_replies_from_one_prospect_count_once(session, product_educore, make_prospect):
    """Un taux de réponse ne peut pas dépasser 100 % : on compte des
    organisations, pas des messages."""
    prospect = make_prospect(product=product_educore)
    _event(session, product_educore, prospect, OutreachEventType.SENT)
    for day in range(3):
        _event(session, product_educore, prospect, OutreachEventType.REPLIED, days_ago=day)
    session.flush()

    kpis = compute_product_kpis(session, product_id=product_educore.id, product_code="educore")
    assert kpis.funnel.replied == 1
    assert kpis.reply_rate == 1.0


def test_blocked_sends_are_counted_as_incidents_not_organizations(
    session, product_educore, make_prospect
):
    """Trois tentatives bloquées sur le même contact sont trois incidents à
    examiner, pas un seul."""
    prospect = make_prospect(product=product_educore)
    for day in range(3):
        _event(session, product_educore, prospect, OutreachEventType.SEND_BLOCKED, days_ago=day)
    session.flush()

    kpis = compute_product_kpis(session, product_id=product_educore.id, product_code="educore")
    assert kpis.funnel.blocked == 3
    assert any("bloqué" in note for note in kpis.notes)


def test_events_outside_the_window_are_excluded(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(session, product_educore, prospect, OutreachEventType.SENT, days_ago=200)
    session.flush()

    assert (
        compute_product_kpis(
            session, product_id=product_educore.id, product_code="educore", window_days=90
        ).funnel.contacted
        == 0
    )
    assert (
        compute_product_kpis(
            session, product_id=product_educore.id, product_code="educore", window_days=365
        ).funnel.contacted
        == 1
    )


def test_products_do_not_contaminate_each_other(
    session, product_educore, product_maintrix, make_prospect
):
    educore_prospect = make_prospect(product=product_educore, domain="e.example.org")
    maintrix_prospect = make_prospect(product=product_maintrix, domain="m.example.org")
    _event(session, product_educore, educore_prospect, OutreachEventType.SENT)
    _event(session, product_maintrix, maintrix_prospect, OutreachEventType.SENT)
    _event(session, product_maintrix, maintrix_prospect, OutreachEventType.REPLIED)
    session.flush()

    kpis = {k.product_code: k for k in compute_all_kpis(session)}
    assert kpis["educore"].funnel.replied == 0
    assert kpis["maintrix"].funnel.replied == 1


def test_kpis_carry_their_computation_date_and_window(session, product_educore):
    kpis = compute_product_kpis(session, product_id=product_educore.id, window_days=30)
    assert kpis.window_days == 30
    assert (kpis.computed_at - kpis.window_start).days == 30


def test_timeseries_fills_empty_days_with_zero(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(session, product_educore, prospect, OutreachEventType.SENT, days_ago=3)
    session.flush()

    series = funnel_timeseries(
        session,
        product_id=product_educore.id,
        event_type=OutreachEventType.SENT,
        window_days=7,
    )
    assert len(series) == 8
    assert sum(point["count"] for point in series) == 1
    assert all("date" in point and "count" in point for point in series)


def test_recording_the_same_event_twice_is_idempotent(session, product_educore, make_prospect):
    """Rejouer un webhook ne doit pas gonfler les indicateurs."""
    prospect = make_prospect(product=product_educore)
    first = _event(session, product_educore, prospect, OutreachEventType.DEMO_HELD)
    second = _event(session, product_educore, prospect, OutreachEventType.DEMO_HELD)
    session.flush()
    assert first.id == second.id
    assert compute_product_kpis(session, product_id=product_educore.id).funnel.demos_held == 1
