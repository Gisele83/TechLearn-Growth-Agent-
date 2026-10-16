"""Sondes, métriques et alertes.

Ce qu'on vérifie ici n'est pas que les chiffres sont justes — les KPI s'en
chargent — mais que la supervision **dit la vérité quand ça va mal**, et
qu'elle ne laisse échapper ni secret ni donnée personnelle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from growth_agent.config import Settings
from growth_agent.models.enums import DeliveryState, OutreachEventType
from growth_agent.models.ops import PurgeRun
from growth_agent.models.outreach import OutreachEvent
from growth_agent.ops.render_alert_rules import render
from growth_agent.retention.runner import (
    PURGE_BLOCKED,
    PURGE_FAILED,
    PURGE_INTERRUPTED,
    PURGE_SUCCEEDED,
)
from growth_agent.services.observability import (
    ALERT_RULES,
    STALE_RESERVATION_MINUTES,
    backup_age_seconds,
    collect,
    evaluate_alerts,
    render_prometheus,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_FILE = REPO_ROOT / "monitoring" / "alerts.rules.yml"


def _event(session, product, **kwargs) -> OutreachEvent:
    defaults = {
        "product_id": product.id,
        "event_type": OutreachEventType.SENT,
        "channel": "email",
        "occurred_at": datetime.now(UTC),
        "actor": "test",
        "payload": {},
        "is_simulated": False,
    }
    event = OutreachEvent(**{**defaults, **kwargs})
    session.add(event)
    session.flush()
    return event


def _samples(session, product_educore, settings=None):
    return collect(session, settings=settings or Settings())


def _value(samples, name, **labels) -> float | None:
    for sample in samples:
        if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
            return sample.value
    return None


# --- Sondes ----------------------------------------------------------------


def test_liveness_never_depends_on_anything(client):
    """Une sonde de vivacité qui interroge la base fait redémarrer
    l'application à chaque panne de base — ce qui ne répare rien."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_reports_the_schema_revision(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert "expected" in body["checks"]["schema_detail"]


def test_readiness_refuses_traffic_when_the_schema_is_behind(client, monkeypatch):
    """Un conteneur démarré avant la fin des migrations répond à tout, et se
    trompe sur la moitié des requêtes. Mieux vaut qu'il se retire."""
    from growth_agent.api.routers import system as system_router
    from growth_agent.db.migrations import RevisionState

    monkeypatch.setattr(
        system_router,
        "revision_state",
        lambda _session: RevisionState(current="0001", expected="0004", up_to_date=False),
    )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_the_probes_are_not_rate_limited():
    """Les limiter revient à se rendre aveugle quand on a besoin de voir."""
    from growth_agent.api.middleware import EXEMPT_PATHS

    assert {"/health/live", "/health/ready", "/metrics"} <= EXEMPT_PATHS


# --- Métriques : accès -----------------------------------------------------


def test_metrics_are_refused_when_no_token_is_configured(client):
    """Jamais ouvert par défaut : un endpoint de métriques laissé ouvert « le
    temps de configurer » ne se referme jamais."""
    assert client.get("/metrics").status_code == 404


def test_metrics_require_the_right_token(client):
    from growth_agent.config import get_settings

    app = client.app
    app.dependency_overrides[get_settings] = lambda: Settings(metrics_token="jeton-de-collecte")
    try:
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers={"Authorization": "Bearer faux"}).status_code == 401
        good = client.get("/metrics", headers={"Authorization": "Bearer jeton-de-collecte"})
        assert good.status_code == 200
        assert good.text.startswith("# HELP")
    finally:
        app.dependency_overrides.pop(get_settings, None)


# --- Métriques : contenu ---------------------------------------------------


def test_no_personal_data_reaches_the_metrics(session, product_educore, make_prospect):
    """Les métriques sont conservées longtemps par un système tiers, hors de
    toute politique de rétention. Une adresse qui y entre n'en sort plus."""
    prospect = make_prospect(product=product_educore, contact_email="cible@exemple.example.org")
    _event(session, product_educore, prospect_id=prospect.id, delivery_state=DeliveryState.SENT)
    session.flush()

    body = render_prometheus(_samples(session, product_educore))
    assert "cible@exemple.example.org" not in body
    assert str(prospect.id) not in body
    assert prospect.organization_name not in body


def test_label_values_stay_within_closed_sets(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(session, product_educore, prospect_id=prospect.id, delivery_state=DeliveryState.SENT)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        event_type=OutreachEventType.SEND_BLOCKED,
        payload={"reason_code": "suppressed"},
    )
    session.flush()

    for sample in _samples(session, product_educore):
        assert set(sample.labels) <= {"product", "mode", "reason", "version", "environment"}
        if "mode" in sample.labels:
            assert sample.labels["mode"] in {"reel", "simule"}


def test_simulated_and_real_sends_are_counted_apart(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.SENT,
        is_simulated=True,
    )
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.SENT,
        is_simulated=False,
    )
    session.flush()
    samples = _samples(session, product_educore)
    assert _value(samples, "growth_agent_sends_24h", product="educore", mode="reel") == 1.0
    assert _value(samples, "growth_agent_sends_24h", product="educore", mode="simule") == 1.0


def test_blocked_sends_are_split_by_reason(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    for reason in ("suppressed", "suppressed", "not_approved"):
        _event(
            session,
            product_educore,
            prospect_id=prospect.id,
            event_type=OutreachEventType.SEND_BLOCKED,
            payload={"reason_code": reason},
        )
    session.flush()
    samples = _samples(session, product_educore)
    assert _value(samples, "growth_agent_sends_blocked_24h", reason="suppressed") == 2.0
    assert _value(samples, "growth_agent_sends_blocked_24h", reason="not_approved") == 1.0


def test_an_uncertain_delivery_is_visible(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.UNCERTAIN,
    )
    session.flush()
    assert _value(_samples(session, product_educore), "growth_agent_deliveries_uncertain") == 1.0


def test_a_fresh_reservation_is_not_reported_as_abandoned(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.RESERVED,
    )
    session.flush()
    assert _value(_samples(session, product_educore), "growth_agent_reservations_stale") == 0.0


def test_an_old_reservation_reveals_a_dead_worker(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.RESERVED,
        occurred_at=datetime.now(UTC) - timedelta(minutes=STALE_RESERVATION_MINUTES + 5),
    )
    session.flush()
    assert _value(_samples(session, product_educore), "growth_agent_reservations_stale") == 1.0


def test_the_metrics_use_the_statuses_the_runner_actually_writes(session, product_educore):
    """Le garde-fou qui compte.

    La supervision filtrait sur un statut inventé (« completed ») que le
    runner n'écrit jamais : la métrique ne sortait pas, et l'alerte « purge en
    retard » n'aurait jamais pu se déclencher. Les tests ne l'avaient pas vu,
    parce qu'ils reprenaient la même erreur. Ce test interroge le runner.
    """
    now = datetime.now(UTC)
    for status in (PURGE_SUCCEEDED, PURGE_FAILED, PURGE_INTERRUPTED, PURGE_BLOCKED):
        session.add(
            PurgeRun(
                status=status,
                dry_run=False,
                started_at=now,
                finished_at=now,
                heartbeat_at=now,
                counts={},
            )
        )
    session.flush()
    samples = _samples(session, product_educore)

    assert _value(samples, "growth_agent_purge_last_success_age_seconds") is not None
    assert _value(samples, "growth_agent_purge_failures_24h") == 2.0  # failed + interrupted
    assert _value(samples, "growth_agent_purge_blocked_24h") == 1.0


def test_a_failed_purge_is_counted(session, product_educore):
    now = datetime.now(UTC)
    session.add(
        PurgeRun(status=PURGE_FAILED, dry_run=False, started_at=now, heartbeat_at=now, counts={})
    )
    session.flush()
    assert _value(_samples(session, product_educore), "growth_agent_purge_failures_24h") == 1.0


def test_a_guardrail_stop_is_not_reported_as_a_failure(session, product_educore):
    """Un arrêt par garde-fou n'est pas une panne : c'est le garde-fou qui
    fait son travail. Le confondre avec un échec enverrait quelqu'un dépanner
    là où il faut comprendre."""
    now = datetime.now(UTC)
    session.add(
        PurgeRun(status=PURGE_BLOCKED, dry_run=False, started_at=now, heartbeat_at=now, counts={})
    )
    session.flush()
    samples = _samples(session, product_educore)
    assert _value(samples, "growth_agent_purge_failures_24h") == 0.0
    assert _value(samples, "growth_agent_purge_blocked_24h") == 1.0


def test_the_age_of_the_last_purge_is_measured(session, product_educore):
    now = datetime.now(UTC)
    session.add(
        PurgeRun(
            status=PURGE_SUCCEEDED,
            dry_run=False,
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=3),
            heartbeat_at=now,
            counts={},
        )
    )
    session.flush()
    age = _value(_samples(session, product_educore), "growth_agent_purge_last_success_age_seconds")
    assert age == pytest.approx(3 * 3600, abs=60)


def test_a_dry_run_does_not_count_as_a_purge(session, product_educore):
    """Une simulation ne supprime rien : la croire suffisante laisserait des
    données dépasser leur durée de conservation sans que rien n'alerte."""
    now = datetime.now(UTC)
    session.add(
        PurgeRun(
            status=PURGE_SUCCEEDED,
            dry_run=True,
            started_at=now,
            finished_at=now,
            heartbeat_at=now,
            counts={},
        )
    )
    session.flush()
    samples = _samples(session, product_educore)
    assert _value(samples, "growth_agent_purge_last_success_age_seconds") is None


def test_real_sending_shows_up_as_a_metric(session, product_educore):
    off = _samples(session, product_educore, Settings())
    assert _value(off, "growth_agent_email_sending_enabled") == 0.0

    on = collect(
        session,
        settings=Settings(
            email_provider="smtp",
            email_sending_enabled=True,
            email_from="contact@exemple.example.org",
            public_base_url="https://go.exemple.example.org",
        ),
    )
    assert _value(on, "growth_agent_email_sending_enabled") == 1.0


# --- Sauvegarde ------------------------------------------------------------


def test_no_configured_stamp_means_no_measurement():
    """L'absence de mesure n'est pas un retard de sauvegarde, et ne doit
    déclencher aucune alerte."""
    assert backup_age_seconds(Settings()) is None


def test_a_missing_stamp_file_means_no_backup_ever_succeeded(tmp_path):
    settings = Settings(backup_stamp_path=str(tmp_path / "jamais-cree"))
    assert backup_age_seconds(settings) == float("inf")


def test_a_recent_stamp_gives_a_small_age(tmp_path):
    stamp = tmp_path / "derniere-sauvegarde"
    stamp.touch()
    age = backup_age_seconds(Settings(backup_stamp_path=str(stamp)))
    assert age is not None and age < 5


def test_an_infinite_age_renders_as_prometheus_expects(tmp_path):
    from growth_agent.services.observability import Sample

    body = render_prometheus([Sample("growth_agent_test", float("inf"))])
    assert "growth_agent_test +Inf" in body


def test_a_never_taken_backup_reads_as_such_in_json(session, product_educore, tmp_path):
    """JSON ne sait pas écrire l'infini : sérialisé tel quel il ressort en
    `null`, qui se lit « aucune mesure » — l'inverse de ce que l'alerte dit."""
    settings = Settings(backup_stamp_path=str(tmp_path / "jamais-cree"))
    firing = evaluate_alerts(collect(session, settings=settings))
    alert = next(a for a in firing if a["name"] == "SauvegardeEnRetard")
    assert alert["value"] is None
    assert alert["value_text"] == "jamais constaté"


# --- Alertes ---------------------------------------------------------------


def test_a_quiet_system_fires_nothing(session, product_educore):
    assert evaluate_alerts(_samples(session, product_educore)) == []


def test_an_uncertain_delivery_fires_a_critical_alert(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.UNCERTAIN,
    )
    session.flush()
    firing = evaluate_alerts(_samples(session, product_educore))
    names = {alert["name"] for alert in firing}
    assert "EnvoisIssueInconnue" in names
    assert firing[0]["severity"] == "critical"


def test_every_alert_carries_a_runbook():
    """Une alerte sans geste associé réveille quelqu'un qui ne saura pas quoi
    faire, et finit par être ignorée."""
    for rule in ALERT_RULES:
        assert rule.runbook.strip(), f"{rule.name} n'indique aucun geste"
        assert len(rule.runbook) > 40, f"{rule.name} : le geste est trop vague"


def test_alerts_are_sorted_with_the_worst_first(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.UNCERTAIN,
    )
    _event(
        session,
        product_educore,
        prospect_id=prospect.id,
        delivery_state=DeliveryState.RESERVED,
        occurred_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.flush()
    firing = evaluate_alerts(_samples(session, product_educore))
    severities = [alert["severity"] for alert in firing]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1}[s])


def test_the_alerts_endpoint_needs_authentication(seeded, client, auth):
    assert client.get("/api/v1/system/alerts").status_code == 401
    assert client.get("/api/v1/system/alerts", headers=auth("lecteur")).status_code == 200


# --- Le fichier de règles ne doit pas diverger du code ---------------------


def test_the_alert_rules_file_matches_the_code():
    """Un seuil défini à deux endroits finit par se contredire, et c'est
    toujours celui qu'on ne regarde pas qui est appliqué."""
    assert RULES_FILE.exists(), f"{RULES_FILE} manquant — l'engendrer avec render_alert_rules"
    assert RULES_FILE.read_text(encoding="utf-8") == render(), (
        "monitoring/alerts.rules.yml a divergé : "
        "python -m growth_agent.ops.render_alert_rules > monitoring/alerts.rules.yml"
    )


def test_the_rules_file_is_valid_yaml_and_covers_every_rule():
    document = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    rules = document["groups"][0]["rules"]
    assert {rule["alert"] for rule in rules} == {rule.name for rule in ALERT_RULES}
    for rule in rules:
        assert rule["annotations"]["runbook"]
        assert rule["labels"]["severity"] in {"critical", "warning"}
