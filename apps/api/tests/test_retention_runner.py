"""Exécution planifiée : verrou, interruption, reprise."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from tests.test_retention import NOW, purge_settings

from growth_agent.models.ops import PurgeRun
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.retention.runner import (
    PurgeLockBusy,
    active_run,
    reclaim_stale_runs,
    run_purge,
)


@pytest.fixture
def expired(session, product_educore, make_prospect):
    def _make(count: int = 3):
        for index in range(count):
            prospect = make_prospect(product=product_educore, domain=f"r{index}.example.org")
            prospect.retention_until = NOW - timedelta(days=30)
        session.commit()

    return _make


def test_dry_run_records_a_run_without_touching_data(session, expired):
    expired(3)
    run, report = run_purge(session, dry_run=True, settings=purge_settings())

    assert run.dry_run is True
    assert run.status == "succeeded"
    assert report.counts["prospects_anonymized"] == 3
    untouched = session.execute(
        select(func.count())
        .select_from(ProspectCandidate)
        .where(ProspectCandidate.anonymized_at.is_(None))
    ).scalar_one()
    assert untouched == 3


def test_real_run_is_recorded_and_applied(session, expired):
    expired(3)
    run, report = run_purge(session, dry_run=False, settings=purge_settings())

    assert run.status == "succeeded"
    assert run.dry_run is False
    assert run.finished_at is not None
    assert run.counts["prospects_anonymized"] == 3
    assert report.total == 3


def test_a_dry_run_does_not_take_the_lock(session, expired):
    """Savoir ce qu'une purge ferait ne doit pas attendre la fin d'une autre."""
    expired(1)
    session.add(
        PurgeRun(
            status="running",
            dry_run=False,
            triggered_by="autre",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            counts={},
        )
    )
    session.commit()

    run, _ = run_purge(session, dry_run=True, settings=purge_settings())
    assert run.status == "succeeded"


def test_a_second_real_run_is_refused(session, expired):
    expired(1)
    session.add(
        PurgeRun(
            status="running",
            dry_run=False,
            triggered_by="autre",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            counts={},
        )
    )
    session.commit()

    with pytest.raises(PurgeLockBusy, match="déjà en cours"):
        run_purge(session, dry_run=False, settings=purge_settings())


def test_a_stale_lock_is_reclaimed(session, expired):
    """Une machine redémarrée en pleine purge ne doit pas bloquer le système
    pour toujours."""
    expired(2)
    stale_moment = datetime.now(UTC) - timedelta(hours=5)
    session.add(
        PurgeRun(
            status="running",
            dry_run=False,
            triggered_by="processus-mort",
            started_at=stale_moment,
            heartbeat_at=stale_moment,
            counts={"prospects_anonymized": 1},
        )
    )
    session.commit()

    reclaimed = reclaim_stale_runs(session, purge_settings())
    assert len(reclaimed) == 1
    assert reclaimed[0].status == "interrupted"
    # Ce que l'exécution morte avait accompli reste consigné.
    assert reclaimed[0].counts == {"prospects_anonymized": 1}
    assert "interrompue" in reclaimed[0].stopped_reason


def test_a_fresh_lock_is_not_reclaimed(session):
    session.add(
        PurgeRun(
            status="running",
            dry_run=False,
            triggered_by="en-cours",
            started_at=datetime.now(UTC),
            heartbeat_at=datetime.now(UTC),
            counts={},
        )
    )
    session.commit()
    assert reclaim_stale_runs(session, purge_settings()) == []
    assert active_run(session, purge_settings()) is not None


def test_run_after_an_interruption_resumes_the_remainder(session, expired):
    """La reprise ne rejoue pas le travail fait : elle traite le reliquat."""
    expired(5)
    settings = purge_settings(purge_batch_size=2, purge_max_records_per_run=2)

    # Première exécution plafonnée : elle n'en traite que deux.
    first, first_report = run_purge(session, dry_run=False, settings=settings)
    assert first_report.counts["prospects_anonymized"] == 2

    # La suivante reprend là où on en était.
    second, second_report = run_purge(session, dry_run=False, settings=settings)
    assert second_report.counts["prospects_anonymized"] == 2

    remaining = session.execute(
        select(func.count())
        .select_from(ProspectCandidate)
        .where(ProspectCandidate.anonymized_at.is_(None))
    ).scalar_one()
    assert remaining == 1


def test_an_interrupted_run_is_referenced_by_the_next_one(session, expired):
    expired(1)
    stale_moment = datetime.now(UTC) - timedelta(hours=5)
    dead = PurgeRun(
        status="running",
        dry_run=False,
        triggered_by="processus-mort",
        started_at=stale_moment,
        heartbeat_at=stale_moment,
        counts={},
    )
    session.add(dead)
    session.commit()
    dead_id = dead.id

    run, _ = run_purge(session, dry_run=False, settings=purge_settings())
    assert run.resumed_from_id == dead_id


def test_the_heartbeat_advances_during_a_run(session, expired):
    expired(6)
    run, _ = run_purge(session, dry_run=False, settings=purge_settings(purge_batch_size=2))
    assert run.heartbeat_at >= run.started_at


def test_a_blocked_run_is_recorded_as_blocked(session, expired):
    expired(1)
    run, report = run_purge(session, dry_run=False, settings=purge_settings(purge_enabled=False))
    assert run.status == "blocked"
    assert "GA_PURGE_ENABLED" in run.stopped_reason


def test_a_blocked_dry_run_reports_blocked_not_succeeded(session, expired):
    """Une simulation annonce ce que ferait une exécution réelle, refus
    compris. Rapporter « succès » pour un plan bloqué serait trompeur."""
    expired(1)
    run, report = run_purge(session, dry_run=True, settings=purge_settings(purge_enabled=False))
    assert run.dry_run is True
    assert run.status == "blocked"
    assert report.plan.blocked is True


def test_a_failure_message_is_stored_without_personal_data(session, expired, monkeypatch):
    """Une erreur SQL porte ses paramètres : l'adresse ne doit pas atterrir
    en clair dans `purge_runs.error_message`."""
    expired(1)

    def exploding_apply(self, **kwargs):
        raise RuntimeError(
            "UPDATE prospect_candidates ... parameters: "
            "('personne@ecole.example.org', '+237 677 889900')"
        )

    from growth_agent.retention.service import PurgeService

    monkeypatch.setattr(PurgeService, "apply", exploding_apply)

    with pytest.raises(RuntimeError):
        run_purge(session, dry_run=False, settings=purge_settings())

    failed = session.execute(select(PurgeRun).where(PurgeRun.status == "failed")).scalars().one()

    assert "personne@ecole.example.org" not in failed.error_message
    assert "m***" not in failed.error_message or "p***" in failed.error_message
    assert "677 889900" not in failed.error_message
    # Le type d'erreur reste exploitable pour le diagnostic.
    assert "RuntimeError" in failed.error_message
