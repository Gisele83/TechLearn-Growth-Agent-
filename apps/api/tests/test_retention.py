"""Purge de rétention.

Ce qui est vérifié ici n'est pas « la purge supprime » mais « la purge ne
supprime pas ce qu'elle ne doit pas ». C'est l'inverse qui compte : une purge
trop timide se corrige, une purge trop zélée est irréversible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from growth_agent.config import Settings
from growth_agent.models.enums import OutreachEventType, SuppressionReason, SuppressionScope
from growth_agent.models.ops import AuditEvent, ProductPilotEvent
from growth_agent.models.outreach import MessageDraft, OutreachEvent, SuppressionEntry
from growth_agent.models.prospect import ProspectCandidate, ProspectSource
from growth_agent.retention.policy import PROTECTED_TABLES, build_policy
from growth_agent.retention.service import PurgeService, SafetyStop
from growth_agent.services.analytics import compute_product_kpis
from growth_agent.services.outreach import record_outreach_event
from growth_agent.services.suppression import add_suppression, check_suppression

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def purge_settings(**overrides) -> Settings:
    """Configuration de purge activée, avec des seuils adaptés aux tests."""
    base = {
        "purge_enabled": True,
        "purge_dry_run_default": True,
        "data_retention_days": 365,
        "draft_retention_days": 180,
        "audit_retention_days": 1095,
        "pilot_event_retention_days": 1825,
        "purge_max_records_per_run": 5000,
        # Les jeux de test sont petits : presque tout y est échu. Le seuil de
        # proportion est donc relevé, sauf dans les tests qui l'éprouvent.
        "purge_max_table_ratio": 1.0,
        "purge_batch_size": 50,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def expired_prospect(session, product_educore, make_prospect):
    """Prospect échu depuis un mois, avec source, brouillon et historique."""
    prospect = make_prospect(
        product=product_educore,
        organization_name="Collège Échu",
        contact_email="direction@echu.example.org",
        domain="echu.example.org",
    )
    prospect.retention_until = NOW - timedelta(days=30)
    session.add(
        MessageDraft(
            product_id=product_educore.id,
            prospect_id=prospect.id,
            recipient_email=prospect.contact_email,
            subject="Objet avec le nom du contact",
            body="Bonjour Contact Test, voici notre proposition. Répondez STOP.",
        )
    )
    record_outreach_event(
        session,
        product_id=product_educore.id,
        prospect_id=prospect.id,
        event_type=OutreachEventType.SENT,
        occurred_at=NOW - timedelta(days=200),
        idempotency_key=f"sent:{prospect.id}",
    )
    session.flush()
    return prospect


# --- Politique ---------------------------------------------------------------


def test_no_rule_targets_a_protected_table():
    """Garde-fou structurel : aucune règle ne peut viser une table protégée."""
    for rule in build_policy(purge_settings()):
        assert rule.table not in PROTECTED_TABLES


def test_suppression_table_is_protected():
    assert "suppression_entries" in PROTECTED_TABLES


def test_every_rule_states_its_rationale():
    """Une durée de conservation sans justification n'est pas défendable
    devant une autorité de contrôle."""
    for rule in build_policy(purge_settings()):
        assert len(rule.rationale) > 60, f"{rule.key} sans justification suffisante"


# --- Simulation --------------------------------------------------------------


def test_dry_run_changes_absolutely_nothing(session, expired_prospect):
    before_email = expired_prospect.contact_email
    before_count = session.execute(select(func.count()).select_from(ProspectCandidate)).scalar_one()

    service = PurgeService(session, settings=purge_settings())
    report = service.apply(dry_run=True, now=NOW)

    session.refresh(expired_prospect)
    assert report.dry_run is True
    assert report.counts["prospects_anonymized"] == 1
    assert expired_prospect.contact_email == before_email
    assert expired_prospect.anonymized_at is None
    assert (
        session.execute(select(func.count()).select_from(ProspectCandidate)).scalar_one()
        == before_count
    )


def test_plan_never_writes(session, expired_prospect):
    service = PurgeService(session, settings=purge_settings())
    plan = service.plan(now=NOW)
    session.refresh(expired_prospect)
    assert plan.total_eligible >= 1
    assert expired_prospect.anonymized_at is None


def test_dry_run_and_real_run_agree(session, expired_prospect):
    """Si la simulation annonce autre chose que ce que fait l'exécution, elle
    ne sert à rien."""
    settings = purge_settings()
    simulated = PurgeService(session, settings=settings).apply(dry_run=True, now=NOW)
    applied = PurgeService(session, settings=settings).apply(dry_run=False, now=NOW)
    assert simulated.counts == applied.counts


# --- Ce qui est préservé -----------------------------------------------------


def test_opt_out_survives_the_purge_of_its_prospect(session, product_educore, expired_prospect):
    """Le cas qui justifie tout le reste.

    Une personne s'est opposée, puis son prospect arrive à échéance. Si la
    purge effaçait son opposition, on la recontacterait — c'est-à-dire qu'on
    lui ferait précisément ce qu'elle a refusé.
    """
    email = expired_prospect.contact_email
    add_suppression(
        session,
        value=email,
        reason=SuppressionReason.OPT_OUT,
        scope=SuppressionScope.GLOBAL,
        now=NOW - timedelta(days=400),
    )
    session.flush()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(expired_prospect)

    # Le prospect est anonymisé…
    assert expired_prospect.contact_email is None
    # …mais l'opposition tient toujours.
    verdict = check_suppression(session, email=email, product_id=product_educore.id, now=NOW)
    assert verdict.suppressed is True
    assert verdict.reason == SuppressionReason.OPT_OUT


def test_suppression_entries_are_never_deleted(session, expired_prospect):
    add_suppression(session, value="vieille@opposition.example.org", now=NOW - timedelta(days=4000))
    session.flush()
    before = session.execute(select(func.count()).select_from(SuppressionEntry)).scalar_one()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)

    after = session.execute(select(func.count()).select_from(SuppressionEntry)).scalar_one()
    assert after == before


def test_legal_hold_freezes_an_expired_record(session, expired_prospect):
    expired_prospect.legal_hold = True
    expired_prospect.legal_hold_reason = "Litige en cours."
    session.flush()

    report = PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(expired_prospect)

    assert expired_prospect.contact_email is not None
    assert expired_prospect.anonymized_at is None
    assert report.counts["prospects_anonymized"] == 0


def test_legal_hold_is_reported_in_the_plan(session, expired_prospect):
    expired_prospect.legal_hold = True
    session.flush()
    plan = PurgeService(session, settings=purge_settings()).plan(now=NOW)
    prospects = next(r for r in plan.rules if r.key == "prospects")
    assert prospects.protected_by_legal_hold == 1
    assert any("blocage légal" in w for w in plan.warnings)


def test_a_record_without_a_due_date_is_never_purged(session, make_prospect, product_educore):
    """Une échéance absente est une inconnue, pas une autorisation."""
    prospect = make_prospect(product=product_educore, domain="sans-echeance.example.org")
    prospect.retention_until = None
    session.flush()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(prospect)
    assert prospect.contact_email is not None


def test_a_record_not_yet_due_is_untouched(session, make_prospect, product_educore):
    prospect = make_prospect(product=product_educore, domain="pas-encore.example.org")
    prospect.retention_until = NOW + timedelta(days=10)
    session.flush()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(prospect)
    assert prospect.contact_email is not None


# --- Ce qui est effacé -------------------------------------------------------


def test_personal_fields_are_erased(session, expired_prospect):
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(expired_prospect)

    assert expired_prospect.contact_email is None
    assert expired_prospect.contact_full_name is None
    assert expired_prospect.contact_phone is None
    assert expired_prospect.contact_role is None
    assert expired_prospect.notes == "[anonymisé]"
    assert expired_prospect.anonymized_at is not None


def test_organization_data_is_kept(session, expired_prospect):
    """L'organisation n'est pas une personne : son nom et son secteur restent,
    sans quoi les indicateurs perdraient tout sens."""
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.refresh(expired_prospect)

    assert expired_prospect.organization_name == "Collège Échu"
    assert expired_prospect.sector is not None
    assert expired_prospect.stage is not None


def test_draft_content_is_erased_for_an_anonymized_prospect(session, expired_prospect):
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    draft = (
        session.execute(select(MessageDraft).where(MessageDraft.prospect_id == expired_prospect.id))
        .scalars()
        .one()
    )

    assert draft.body == "[anonymisé]"
    assert draft.recipient_email is None
    # La ligne subsiste : le fait qu'un message ait existé reste auditable.
    assert draft.status is not None
    assert draft.created_at is not None


def test_source_excerpt_is_erased_but_provenance_is_kept(session, expired_prospect):
    """L'URL et la date prouvent pourquoi ce prospect avait été retenu : les
    effacer rendrait la décision indéfendable a posteriori."""
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    source = (
        session.execute(
            select(ProspectSource).where(ProspectSource.prospect_id == expired_prospect.id)
        )
        .scalars()
        .first()
    )

    assert source.excerpt == "[anonymisé]"
    assert source.url.startswith("https://")
    assert source.collected_at is not None


# --- Stabilité des indicateurs -----------------------------------------------


def test_kpis_do_not_move_after_a_purge(session, product_educore, expired_prospect):
    """Le point le plus important de la conception.

    Si purger changeait les chiffres du trimestre passé, les indicateurs ne
    seraient plus une mesure mais une opinion du moment.
    """
    before = compute_product_kpis(session, product_id=product_educore.id, window_days=365, now=NOW)
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    after = compute_product_kpis(session, product_id=product_educore.id, window_days=365, now=NOW)

    assert before.funnel == after.funnel


def test_outreach_events_survive_the_purge(session, expired_prospect):
    before = session.execute(select(func.count()).select_from(OutreachEvent)).scalar_one()
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    after = session.execute(select(func.count()).select_from(OutreachEvent)).scalar_one()
    assert after == before


# --- Idempotence et reprise --------------------------------------------------


def test_purging_twice_changes_nothing_the_second_time(session, expired_prospect):
    settings = purge_settings()
    first = PurgeService(session, settings=settings).apply(dry_run=False, now=NOW)
    second = PurgeService(session, settings=settings).apply(dry_run=False, now=NOW)

    assert first.counts["prospects_anonymized"] == 1
    assert second.counts["prospects_anonymized"] == 0


def test_batching_processes_everything(session, product_educore, make_prospect):
    """Avec des lots plus petits que le volume, rien ne doit être oublié."""
    for index in range(12):
        prospect = make_prospect(product=product_educore, domain=f"lot{index}.example.org")
        prospect.retention_until = NOW - timedelta(days=5)
    session.flush()

    report = PurgeService(session, settings=purge_settings(purge_batch_size=5)).apply(
        dry_run=False, now=NOW
    )
    assert report.counts["prospects_anonymized"] == 12

    remaining = session.execute(
        select(func.count())
        .select_from(ProspectCandidate)
        .where(ProspectCandidate.anonymized_at.is_(None))
    ).scalar_one()
    assert remaining == 0


# --- Garde-fous --------------------------------------------------------------


def test_purge_disabled_blocks_execution(session, expired_prospect):
    report = PurgeService(session, settings=purge_settings(purge_enabled=False)).apply(
        dry_run=False, now=NOW
    )
    session.refresh(expired_prospect)

    assert report.plan.blocked is True
    assert "GA_PURGE_ENABLED" in report.stopped_reason
    assert expired_prospect.contact_email is not None


def test_abnormal_proportion_stops_before_any_write(session, product_educore, make_prospect):
    """Le garde-fou qui rattrape une erreur de calcul de date.

    Si une durée de conservation mal saisie rendait toute la base « échue », ce
    seuil l'arrête avant la première écriture.
    """
    for index in range(10):
        prospect = make_prospect(product=product_educore, domain=f"tout{index}.example.org")
        prospect.retention_until = NOW - timedelta(days=1)
    session.flush()

    settings = purge_settings(purge_max_table_ratio=0.5)
    report = PurgeService(session, settings=settings).apply(dry_run=False, now=NOW)

    assert report.plan.blocked is True
    assert "Proportion anormale" in report.stopped_reason
    untouched = session.execute(
        select(func.count())
        .select_from(ProspectCandidate)
        .where(ProspectCandidate.anonymized_at.is_(None))
    ).scalar_one()
    assert untouched == 10


def test_record_cap_limits_a_single_run(session, product_educore, make_prospect):
    for index in range(8):
        prospect = make_prospect(product=product_educore, domain=f"cap{index}.example.org")
        prospect.retention_until = NOW - timedelta(days=5)
    session.flush()

    report = PurgeService(session, settings=purge_settings(purge_max_records_per_run=3)).apply(
        dry_run=False, now=NOW
    )

    assert report.counts["prospects_anonymized"] == 3
    assert "Plafond" in report.stopped_reason


def test_a_rule_targeting_a_protected_table_raises(session):
    from growth_agent.retention.policy import RetentionPolicy, RetentionRule

    rogue = RetentionPolicy(
        rules=(
            RetentionRule(
                key="rogue",
                label="Règle fautive",
                table="suppression_entries",
                retention_days=1,
                action="delete",
                rationale="x" * 70,
            ),
        )
    )
    with pytest.raises(SafetyStop, match="protégée"):
        PurgeService(session, settings=purge_settings(), policy=rogue).plan(now=NOW)


# --- Autres classes de donnée ------------------------------------------------


def test_old_audit_events_are_deleted(session, product_educore):
    from growth_agent.services.audit import record_audit

    record_audit(
        session,
        actor="a@b.example.org",
        action="test.ancien",
        object_type="test",
        now=NOW - timedelta(days=1200),
    )
    record_audit(
        session,
        actor="a@b.example.org",
        action="test.recent",
        object_type="test",
        now=NOW - timedelta(days=10),
    )
    session.flush()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)

    actions = [
        e.action
        for e in session.execute(select(AuditEvent)).scalars()
        if e.action.startswith("test.")
    ]
    assert "test.recent" in actions
    assert "test.ancien" not in actions


def test_old_pilot_events_are_deleted(session, product_educore):
    session.add(
        ProductPilotEvent(
            product_id=product_educore.id,
            event_type="pilot.activated",
            external_event_id="vieux-1",
            occurred_at=NOW - timedelta(days=2000),
            received_at=NOW - timedelta(days=2000),
        )
    )
    session.add(
        ProductPilotEvent(
            product_id=product_educore.id,
            event_type="pilot.activated",
            external_event_id="recent-1",
            occurred_at=NOW - timedelta(days=30),
            received_at=NOW - timedelta(days=30),
        )
    )
    session.flush()

    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)

    remaining = [e.external_event_id for e in session.execute(select(ProductPilotEvent)).scalars()]
    assert remaining == ["recent-1"]


# --- Journalisation ----------------------------------------------------------


def test_purge_is_audited_without_personal_data(session, expired_prospect):
    email = expired_prospect.contact_email
    PurgeService(session, settings=purge_settings()).apply(
        dry_run=False, now=NOW, actor="scheduler"
    )

    entry = (
        session.execute(select(AuditEvent).where(AuditEvent.action == "retention.purge_applied"))
        .scalars()
        .one()
    )

    assert entry.metadata_json["counts"]["prospects_anonymized"] == 1
    # Ni l'adresse purgée, ni aucun identifiant de personne.
    assert email not in str(entry.metadata_json)
    assert "echu.example.org" not in str(entry.metadata_json)


def test_dry_run_is_not_audited_as_an_application(session, expired_prospect):
    """Une simulation n'a rien modifié : elle ne doit pas laisser croire le
    contraire dans le journal."""
    PurgeService(session, settings=purge_settings()).apply(dry_run=True, now=NOW)
    entries = session.execute(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "retention.purge_applied")
    ).scalar_one()
    assert entries == 0
