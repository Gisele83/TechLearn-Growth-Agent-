"""Interface d'envoi d'email.

Le connecteur est le dernier maillon : il ne décide rien. La vérification de
l'approbation et de la liste d'opposition a lieu en amont, dans le service
d'envoi. Un fournisseur ne doit jamais être appelé directement par un agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class EmailSendingDisabled(Exception):
    """L'envoi réel est désactivé par la configuration."""


@dataclass(slots=True)
class EmailMessage:
    to_email: str
    subject: str
    body: str
    from_email: str = ""
    reply_to: str | None = None
    campaign_id: str = ""
    product_code: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    def unsubscribe_headers(self, unsubscribe_url: str) -> dict[str, str]:
        """En-têtes de désinscription en un clic (RFC 8058).

        Exigés par les principaux fournisseurs de messagerie pour les envois
        commerciaux ; leur absence dégrade fortement la délivrabilité.
        """
        return {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }


@dataclass(slots=True)
class EmailSendResult:
    accepted: bool
    provider: str
    message_id: str = ""
    sent_at: datetime | None = None
    detail: str = ""


class EmailProvider(ABC):
    provider: str = "abstract"

    @abstractmethod
    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        ...

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": "ok"}
