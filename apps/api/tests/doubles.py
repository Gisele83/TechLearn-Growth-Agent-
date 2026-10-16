"""Doublures partagées par les tests d'envoi.

Aucune d'elles n'ouvre de connexion : rien ne quitte jamais la machine. Ce
qui les distingue du `MockEmailProvider` du code applicatif, c'est leur
`provider` — il ne vaut pas « mock », donc le service les traite comme un
fournisseur réel et marque les événements comme réels. C'est indispensable
pour tester les plafonds de fréquence et la séparation simulé/réel, que le
mock contourne par construction.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

from growth_agent.config import Settings
from growth_agent.connectors.email.base import EmailMessage, EmailProvider, EmailSendResult


class Crash(BaseException):
    """Panne brutale du processus.

    Volontairement hors de `Exception` : aucun `except` du service ne peut
    l'intercepter, ce qui reproduit fidèlement un processus tué net.
    """


class ScriptedProvider(EmailProvider):
    """Fournisseur piloté par le test, sûr pour un usage concurrent.

    `behaviour` choisit ce qui arrive à l'appel :

    * `accept` — remise acceptée ;
    * `refuse` — refus explicite, rien n'est parti ;
    * `timeout` — pas de réponse lisible, issue inconnue ;
    * `crash_before_handoff` — le processus meurt avant la remise ;
    * `crash_after_handoff` — il meurt après la remise, avant la confirmation.
    """

    provider = "smtp"

    def __init__(self, behaviour: str = "accept") -> None:
        self.behaviour = behaviour
        self._lock = threading.Lock()
        self.outbox: list[EmailMessage] = []
        self.calls: list[str] = []

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        with self._lock:
            self.calls.append(idempotency_key)
        if self.behaviour == "crash_before_handoff":
            raise Crash("coupure avant remise au fournisseur")
        if self.behaviour == "timeout":
            raise TimeoutError("le fournisseur n'a pas répondu")
        if self.behaviour == "refuse":
            return EmailSendResult(
                accepted=False,
                provider=self.provider,
                detail="Adresse rejetée par le fournisseur.",
            )
        with self._lock:
            self.outbox.append(message)
        if self.behaviour == "crash_after_handoff":
            raise Crash("coupure après remise, avant confirmation")
        return EmailSendResult(
            accepted=True,
            provider=self.provider,
            message_id=f"smtp-{uuid.uuid4().hex[:16]}",
            sent_at=datetime.now(UTC),
        )


def sending_settings(**overrides) -> Settings:
    """Configuration autorisant l'envoi réel. Aucun message ne part pour
    autant : le fournisseur reste une doublure."""
    defaults = {
        "email_provider": "smtp",
        "email_sending_enabled": True,
        "email_from": "contact@exemple.example.org",
        "public_base_url": "https://go.exemple.example.org",
    }
    return Settings(**{**defaults, **overrides})
