"""Envoi SMTP réel — inactif tant qu'il n'est pas explicitement autorisé.

Le constructeur refuse de s'initialiser si `email_sending_enabled` est faux.
C'est délibéré : le chemin d'envoi réel ne doit pas pouvoir être emprunté par
erreur, même si une variable de configuration est mal renseignée.

Avant d'activer ce fournisseur :
  * domaine d'expédition dédié à la prospection, distinct des emails
    transactionnels Maintrix et EduCore ;
  * SPF, DKIM et DMARC publiés et vérifiés ;
  * base légale confirmée pour chaque pays ciblé (voir docs/07) ;
  * mécanisme d'opposition opérationnel et testé.
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage as MIMEEmailMessage
from typing import Any

from growth_agent.connectors.email.base import (
    EmailMessage,
    EmailProvider,
    EmailSendingDisabled,
    EmailSendResult,
)

#: Exceptions qui annoncent un refus **définitif** du serveur, par opposition
#: à une coupure ou un délai dépassé, dont l'issue est inconnue.
PERMANENT_REFUSALS = (
    smtplib.SMTPRecipientsRefused,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPDataError,
    smtplib.SMTPNotSupportedError,
)


class SMTPEmailProvider(EmailProvider):
    provider = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_email: str,
        sending_enabled: bool,
        reply_to: str = "",
        timeout: float = 20.0,
        smtp_factory: Any = None,
    ) -> None:
        if not sending_enabled:
            raise EmailSendingDisabled(
                "GA_EMAIL_SENDING_ENABLED est faux : l'envoi réel est interdit."
            )
        if not (host and from_email):
            raise EmailSendingDisabled("Configuration SMTP incomplète : envoi refusé.")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_email = from_email
        self._reply_to = reply_to
        self._timeout = timeout
        self._smtp_factory = smtp_factory or smtplib.SMTP
        self._sent_keys: set[str] = set()

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        if idempotency_key in self._sent_keys:
            return EmailSendResult(
                accepted=True,
                provider=self.provider,
                message_id=f"replay-{idempotency_key[:24]}",
                detail="Envoi déjà effectué pour cette clé d'idempotence.",
            )

        mime = MIMEEmailMessage()
        mime["From"] = message.from_email or self._from_email
        mime["To"] = message.to_email
        mime["Subject"] = message.subject
        reply_to = message.reply_to or self._reply_to
        if reply_to:
            mime["Reply-To"] = reply_to
        for key, value in message.headers.items():
            mime[key] = value
        mime.set_content(message.body)

        context = ssl.create_default_context()
        try:
            with self._smtp_factory(self._host, self._port, timeout=self._timeout) as client:
                client.starttls(context=context)
                if self._username:
                    client.login(self._username, self._password)
                client.send_message(mime)
        except PERMANENT_REFUSALS as exc:
            # Refus définitif annoncé par le serveur : destinataire inconnu,
            # expéditeur rejeté, message refusé. Rien n'est parti. Sans cette
            # distinction, l'exception remonterait au service d'envoi, qui
            # classerait l'issue comme « inconnue » — et le message resterait
            # bloqué pour toujours en attente d'un arbitrage qui n'avait pas
            # lieu d'être.
            return EmailSendResult(
                accepted=False,
                provider=self.provider,
                detail=f"Le serveur SMTP a refusé le message ({type(exc).__name__}).",
            )

        self._sent_keys.add(idempotency_key)
        return EmailSendResult(
            accepted=True,
            provider=self.provider,
            message_id=mime.get("Message-ID", f"smtp-{idempotency_key[:24]}"),
            sent_at=datetime.now(UTC),
        )

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": "configured", "host": self._host}
