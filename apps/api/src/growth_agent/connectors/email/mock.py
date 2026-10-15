"""Fournisseur d'email factice : rien ne quitte la machine.

Les messages sont conservés en mémoire et consultables par les tests et par
l'interface (« boîte d'envoi simulée »). C'est le mode par défaut, et le seul
disponible tant que le domaine d'expédition et le cadre juridique n'ont pas été
validés.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from growth_agent.connectors.email.base import EmailMessage, EmailProvider, EmailSendResult


class MockEmailProvider(EmailProvider):
    provider = "mock"

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []
        self._by_key: dict[str, EmailSendResult] = {}

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        # Idempotence : un même envoi rejoué ne duplique pas le message.
        if idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        self.outbox.append(message)
        result = EmailSendResult(
            accepted=True,
            provider=self.provider,
            message_id=f"mock-{idempotency_key[:24]}",
            sent_at=datetime.now(UTC),
            detail="Envoi simulé : aucun message n'a quitté la machine.",
        )
        self._by_key[idempotency_key] = result
        return result

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": "ok", "outbox_size": len(self.outbox)}

    def clear(self) -> None:
        self.outbox.clear()
        self._by_key.clear()
