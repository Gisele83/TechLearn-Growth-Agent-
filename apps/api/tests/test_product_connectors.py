"""Connecteurs produits : périmètre de lecture et signature des webhooks."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from growth_agent.connectors.product.http import ALLOWED_EVENT_TYPES, HTTPProductAdapter
from growth_agent.connectors.product.webhooks import (
    WebhookVerificationError,
    compute_signature,
    verify_webhook_signature,
)


def _adapter(handler) -> HTTPProductAdapter:
    return HTTPProductAdapter(
        product_code="educore",
        base_url="https://educore.test",
        api_token="jeton",
        enabled=True,
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://educore.test"
        ),
    )


def test_only_the_four_contracted_event_types_are_accepted():
    assert ALLOWED_EVENT_TYPES == {
        "pilot.invited",
        "pilot.activated",
        "pilot.feedback_received",
        "license.status_changed",
    }


def test_out_of_scope_events_are_discarded_at_the_boundary():
    """Si la plateforme se met à émettre d'autres événements, Growth Agent les
    ignore au lieu de les stocker."""

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "1",
                        "type": "pilot.activated",
                        "organization_ref": "org-1",
                        "occurred_at": "2026-10-01T10:00:00Z",
                    },
                    {
                        "id": "2",
                        "type": "student.grade_updated",
                        "organization_ref": "org-1",
                        "occurred_at": "2026-10-02T10:00:00Z",
                    },
                ]
            },
        )

    events = _adapter(handler).list_events()
    assert [e.event_type for e in events] == ["pilot.activated"]


def test_unexpected_payload_fields_are_stripped():
    """Même dans un événement autorisé, seuls les champs du contrat passent :
    aucune donnée d'élève ou d'équipement n'entre dans la base."""

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "1",
                        "type": "pilot.feedback_received",
                        "organization_ref": "org-1",
                        "occurred_at": "2026-10-01T10:00:00Z",
                        "data": {
                            "shareable": True,
                            "comment": "Utile.",
                            "student_name": "Nom d'élève",
                            "student_grades": [12, 15],
                            "equipment_serial": "ABC-123",
                        },
                    }
                ]
            },
        )

    payload = _adapter(handler).list_events()[0].payload
    assert payload == {"shareable": True, "comment": "Utile."}
    assert "student_name" not in payload
    assert "equipment_serial" not in payload


def test_events_without_a_valid_date_are_discarded():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "events": [
                    {"id": "1", "type": "pilot.invited", "organization_ref": "o", "occurred_at": ""}
                ]
            },
        )

    assert _adapter(handler).list_events() == []


def test_unknown_organization_returns_none():
    def handler(_request):
        return httpx.Response(404)

    assert _adapter(handler).get_pilot_snapshot("inconnue") is None


def test_adapter_declares_itself_read_only():
    def handler(_request):
        return httpx.Response(200, json={})

    assert _adapter(handler).is_read_only is True


# --- Webhooks ----------------------------------------------------------------


def test_a_correctly_signed_webhook_is_accepted():
    body = b'{"type":"pilot.activated"}'
    timestamp = str(int(time.time()))
    signature = compute_signature("secret-partage", timestamp, body)
    verify_webhook_signature(
        secret="secret-partage", timestamp=timestamp, signature=signature, body=body
    )


def test_a_tampered_body_is_refused():
    body = b'{"type":"pilot.activated"}'
    timestamp = str(int(time.time()))
    signature = compute_signature("secret-partage", timestamp, body)
    with pytest.raises(WebhookVerificationError, match="Signature invalide"):
        verify_webhook_signature(
            secret="secret-partage",
            timestamp=timestamp,
            signature=signature,
            body=b'{"type":"license.status_changed"}',
        )


def test_a_wrong_secret_is_refused():
    body = b"x"
    timestamp = str(int(time.time()))
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(
            secret="bon-secret",
            timestamp=timestamp,
            signature=compute_signature("mauvais-secret", timestamp, body),
            body=body,
        )


def test_a_replayed_webhook_is_refused():
    """Une requête interceptée et rejouée plus tard est hors fenêtre."""
    body = b"x"
    old_timestamp = str(int(time.time()) - 3600)
    with pytest.raises(WebhookVerificationError, match="hors fenêtre"):
        verify_webhook_signature(
            secret="s",
            timestamp=old_timestamp,
            signature=compute_signature("s", old_timestamp, body),
            body=body,
        )


def test_an_unsigned_webhook_is_refused():
    with pytest.raises(WebhookVerificationError, match="manquant"):
        verify_webhook_signature(secret="s", timestamp="", signature="", body=b"x")


def test_no_configured_secret_means_refusal():
    with pytest.raises(WebhookVerificationError, match="Aucun secret"):
        verify_webhook_signature(secret="", timestamp="1", signature="a", body=b"x")


def test_mock_adapters_expose_a_coherent_pilot_lifecycle(educore_adapter, maintrix_adapter):
    for adapter in (educore_adapter, maintrix_adapter):
        events = adapter.list_events()
        assert events == sorted(events, key=lambda e: e.occurred_at)
        assert all(e.event_type in ALLOWED_EVENT_TYPES for e in events)


def test_since_filter_narrows_the_event_window(educore_adapter):
    recent = datetime.now(UTC) - timedelta(days=10)
    assert len(educore_adapter.list_events(since=recent)) < len(educore_adapter.list_events())
