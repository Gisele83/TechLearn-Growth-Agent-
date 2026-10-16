"""Intégration SendGrid réelle — en bac à sable, donc sans rien livrer.

**Exclus par défaut** (marqueur `integration`). Lancement :

    GA_SENDGRID_TEST_KEY="SG.…" pytest -m integration tests/test_email_integration.py

Ce que seul le vrai service peut dire, et qu'aucune doublure ne prouvera :

* la clé d'API est valide et porte le droit `mail.send` ;
* `noreply@techlearn-saem.com` est bien une identité d'expéditeur vérifiée
  sur ce compte ;
* la forme de notre charge est acceptée telle quelle, en-têtes compris.

Le bac à sable est **imposé par le test, pas par la configuration** : même
lancé avec une clé de production, rien ne peut partir. C'est la seule façon
honnête de parler au vrai service avant d'avoir le droit d'envoyer.

Ce fichier ne couvre toujours pas le parcours complet demandé — envoi vers
une boîte de test, réponse STOP, réception. La réception n'existe pas.
"""

from __future__ import annotations

import os

import pytest

from growth_agent.connectors.email.base import EmailMessage
from growth_agent.connectors.email.sendgrid import SendGridEmailProvider

API_KEY = os.environ.get("GA_SENDGRID_TEST_KEY", "")
SENDER = os.environ.get("GA_SENDGRID_TEST_SENDER", "noreply@techlearn-saem.com")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not API_KEY, reason="GA_SENDGRID_TEST_KEY non défini : intégration SendGrid ignorée"
    ),
]


@pytest.fixture
def sandbox() -> SendGridEmailProvider:
    provider = SendGridEmailProvider(
        api_key=API_KEY,
        from_email=SENDER,
        sending_enabled=True,
        # Jamais paramétrable ici : le test ne doit pas pouvoir livrer.
        sandbox_mode=True,
    )
    try:
        yield provider
    finally:
        provider.close()


def _message(**kwargs) -> EmailMessage:
    # Domaine réservé par la RFC 2606 : même sans bac à sable, il n'existe
    # aucune boîte derrière.
    defaults = {
        "to_email": "recette@exemple.example.org",
        "subject": "Recette TechLearn — bac à sable",
        "body": "Message de recette. Rien ne doit être livré.",
        "from_email": SENDER,
    }
    return EmailMessage(**{**defaults, **kwargs})


def test_the_api_key_and_the_verified_sender_are_accepted(sandbox):
    """Si l'expéditeur n'était pas vérifié, SendGrid répondrait 403 — et le
    connecteur le rapporterait comme un refus, pas comme une panne."""
    result = sandbox.send(_message(), idempotency_key="recette-expediteur")
    assert result.accepted is True, result.detail
    assert "rien n'a été livré" in result.detail


def test_our_payload_is_accepted_headers_included(sandbox):
    message = _message()
    message.headers.update(
        message.unsubscribe_headers("https://go.techlearn-saem.com/unsubscribe/jeton-de-recette")
    )
    result = sandbox.send(message, idempotency_key="recette-entetes")
    assert result.accepted is True, result.detail


def test_an_unverified_sender_is_reported_as_a_refusal(sandbox):
    """Le cas qu'on veut voir échouer proprement : un refus doit libérer la
    réservation, pas geler le message en « issue inconnue »."""
    result = sandbox.send(
        _message(from_email="inconnu@domaine-non-verifie.example"),
        idempotency_key="recette-expediteur-inconnu",
    )
    assert result.accepted is False
    assert result.detail
