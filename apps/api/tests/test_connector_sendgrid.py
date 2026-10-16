"""Connecteur SendGrid.

Le point qui compte : **les trois issues d'envoi restent distinctes.** Le
protocole du lot 1 en dépend — un refus définitif libère la réservation et
autorise une nouvelle tentative, une issue inconnue la gèle en attendant un
arbitrage humain. Confondre les deux condamne un message pour rien, ou en
envoie un deuxième à quelqu'un qui l'a déjà reçu.
"""

from __future__ import annotations

import json

import httpx
import pytest

from growth_agent.config import Settings
from growth_agent.connectors.email.base import EmailMessage, EmailSendingDisabled
from growth_agent.connectors.email.factory import get_email_provider, reset_email_singleton
from growth_agent.connectors.email.sendgrid import (
    SendGridEmailProvider,
    SendGridUnavailable,
)

VERIFIED_SENDER = "noreply@techlearn-saem.com"


class Portal:
    """Portail SendGrid simulé : on choisit la réponse, on relit la charge."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses) or [httpx.Response(202, headers={"X-Message-Id": "sg-1"})]
        self.payloads: list[dict] = []
        self.calls: list[str] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        self.payloads.append(json.loads(request.content or b"{}"))
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

    def client(self) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(self._handle),
            base_url="https://api.sendgrid.test",
            headers={"Authorization": "Bearer SG.jeton-de-test"},
        )


def _provider(portal: Portal, **kwargs) -> SendGridEmailProvider:
    return SendGridEmailProvider(
        api_key="SG.jeton-de-test",
        from_email=VERIFIED_SENDER,
        sending_enabled=True,
        client=portal.client(),
        sleep=lambda _s: None,
        **kwargs,
    )


def _message(**kwargs) -> EmailMessage:
    defaults = {
        "to_email": "contact@exemple.example.org",
        "subject": "Un objet",
        "body": "Bonjour.",
        "from_email": VERIFIED_SENDER,
    }
    return EmailMessage(**{**defaults, **kwargs})


# --- Le double verrou tient -------------------------------------------------


def test_the_provider_refuses_to_exist_when_sending_is_disabled():
    with pytest.raises(EmailSendingDisabled):
        SendGridEmailProvider(api_key="SG.x", from_email=VERIFIED_SENDER, sending_enabled=False)


def test_the_provider_refuses_to_exist_without_an_api_key():
    with pytest.raises(EmailSendingDisabled):
        SendGridEmailProvider(api_key="", from_email=VERIFIED_SENDER, sending_enabled=True)


def test_the_factory_falls_back_to_the_mock_without_a_key():
    reset_email_singleton()
    settings = Settings(
        email_provider="sendgrid",
        email_sending_enabled=True,
        email_from=VERIFIED_SENDER,
        public_base_url="https://go.techlearn-saem.com",
        sendgrid_api_key="",
    )
    assert settings.can_send_email() is False
    assert get_email_provider(settings).provider == "mock"
    reset_email_singleton()


def test_the_factory_builds_sendgrid_when_everything_is_in_place():
    reset_email_singleton()
    settings = Settings(
        email_provider="sendgrid",
        email_sending_enabled=True,
        email_from=VERIFIED_SENDER,
        public_base_url="https://go.techlearn-saem.com",
        sendgrid_api_key="SG.jeton",
    )
    provider = get_email_provider(settings)
    assert provider.provider == "sendgrid"
    assert provider.healthcheck()["sandbox_mode"] is True  # bac à sable par défaut
    reset_email_singleton()


# --- Les trois issues -------------------------------------------------------


def test_an_accepted_message_is_accepted():
    portal = Portal(httpx.Response(202, headers={"X-Message-Id": "sg-abc"}))
    result = _provider(portal).send(_message(), idempotency_key="k-1")
    assert result.accepted is True
    assert result.message_id == "sg-abc"


def test_a_rejected_message_is_a_refusal_not_an_unknown_outcome():
    """Un refus définitif doit libérer la réservation. Classé « inconnu », le
    message resterait bloqué pour toujours en attente d'un arbitrage."""
    portal = Portal(
        httpx.Response(
            400,
            json={"errors": [{"message": "Does not contain a valid address."}]},
        )
    )
    result = _provider(portal).send(_message(), idempotency_key="k-2")
    assert result.accepted is False
    assert "valid address" in result.detail
    assert len(portal.calls) == 1, "un 4xx ne doit pas être réessayé"


def test_an_unverified_sender_is_a_refusal():
    portal = Portal(
        httpx.Response(
            403,
            json={
                "errors": [
                    {"message": "The from address does not match a verified Sender Identity."}
                ]
            },
        )
    )
    result = _provider(portal).send(_message(), idempotency_key="k-3")
    assert result.accepted is False
    assert "verified Sender" in result.detail


def test_a_server_failure_raises_rather_than_claiming_a_refusal():
    """Après épuisement des réessais, l'issue est inconnue — pas un échec. Le
    service d'envoi doit geler le message, pas le rouvrir."""
    portal = Portal(httpx.Response(503, json={}))
    with pytest.raises(SendGridUnavailable):
        _provider(portal, max_retries=2).send(_message(), idempotency_key="k-4")
    assert len(portal.calls) == 3  # tentative initiale + 2 réessais


def test_a_rate_limit_is_retried_then_succeeds():
    portal = Portal(
        httpx.Response(429, headers={"Retry-After": "0"}, json={}),
        httpx.Response(202, headers={"X-Message-Id": "sg-apres-quota"}),
    )
    result = _provider(portal, max_retries=2).send(_message(), idempotency_key="k-5")
    assert result.accepted is True
    assert len(portal.calls) == 2


# --- Ce qui part réellement dans la charge ----------------------------------


def test_nothing_is_tracked():
    """Un pixel de traçage collecte des données sans consentement, et des
    liens réécrits font passer la désinscription par un tiers."""
    portal = Portal()
    _provider(portal).send(_message(), idempotency_key="k-6")
    tracking = portal.payloads[0]["tracking_settings"]
    assert tracking["click_tracking"]["enable"] is False
    assert tracking["open_tracking"]["enable"] is False
    assert tracking["subscription_tracking"]["enable"] is False


def test_the_unsubscribe_headers_survive_intact():
    """RFC 8058 : sans ces en-têtes, le bouton natif de Gmail disparaît."""
    portal = Portal()
    message = _message()
    message.headers.update(
        message.unsubscribe_headers("https://go.techlearn-saem.com/unsubscribe/x")
    )
    _provider(portal).send(message, idempotency_key="k-7")
    headers = portal.payloads[0]["headers"]
    assert headers["List-Unsubscribe"] == "<https://go.techlearn-saem.com/unsubscribe/x>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_headers_sendgrid_owns_are_not_imposed():
    portal = Portal()
    message = _message()
    message.headers.update({"To": "pirate@exemple.example.org", "X-Libre": "ok"})
    _provider(portal).send(message, idempotency_key="k-8")
    headers = portal.payloads[0].get("headers", {})
    assert "To" not in headers
    assert headers["X-Libre"] == "ok"


def test_the_reply_address_is_carried():
    portal = Portal()
    _provider(portal, reply_to="contact@techlearn-saem.com").send(_message(), idempotency_key="k-9")
    assert portal.payloads[0]["reply_to"] == {"email": "contact@techlearn-saem.com"}


def test_without_a_reply_address_none_is_declared():
    portal = Portal()
    _provider(portal).send(_message(), idempotency_key="k-10")
    assert "reply_to" not in portal.payloads[0]


def test_the_idempotency_key_travels_as_a_correlation_marker():
    portal = Portal()
    _provider(portal).send(_message(), idempotency_key="send:abcdef")
    assert portal.payloads[0]["custom_args"]["idempotency_key"] == "send:abcdef"


def test_no_recipient_address_leaks_into_the_correlation_marker():
    portal = Portal()
    _provider(portal).send(_message(), idempotency_key="send:abcdef")
    assert "exemple.example.org" not in json.dumps(portal.payloads[0]["custom_args"])


# --- Bac à sable ------------------------------------------------------------


def test_sandbox_mode_is_declared_to_sendgrid():
    portal = Portal()
    _provider(portal, sandbox_mode=True).send(_message(), idempotency_key="k-11")
    assert portal.payloads[0]["mail_settings"]["sandbox_mode"]["enable"] is True


def test_sandbox_mode_says_so_in_the_result():
    portal = Portal()
    result = _provider(portal, sandbox_mode=True).send(_message(), idempotency_key="k-12")
    assert result.accepted is True
    assert "rien n'a été livré" in result.detail


def test_outside_sandbox_nothing_is_declared():
    portal = Portal()
    _provider(portal, sandbox_mode=False).send(_message(), idempotency_key="k-13")
    assert "mail_settings" not in portal.payloads[0]


# --- Supervision ------------------------------------------------------------


def test_the_healthcheck_never_reveals_the_key():
    portal = Portal()
    report = _provider(portal).healthcheck()
    serialized = json.dumps(report)
    assert "SG.jeton-de-test" not in serialized
    assert "jeton" not in serialized
    assert report["sender_domain"] == "techlearn-saem.com"
