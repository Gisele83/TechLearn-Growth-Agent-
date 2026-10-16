"""Sélection du fournisseur d'email.

Double verrou : il faut `GA_EMAIL_SENDING_ENABLED=true` ET un fournisseur
non-mock ET un expéditeur renseigné. Sinon, le mock — aucun envoi réel.
"""

from __future__ import annotations

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.email.base import EmailProvider
from growth_agent.connectors.email.mock import MockEmailProvider
from growth_agent.connectors.email.sendgrid import SendGridEmailProvider
from growth_agent.connectors.email.smtp import SMTPEmailProvider

_mock_singleton: MockEmailProvider | None = None


def get_email_provider(settings: Settings | None = None) -> EmailProvider:
    global _mock_singleton
    settings = settings or get_settings()

    if settings.can_send_email() and settings.email_provider == "sendgrid":
        return SendGridEmailProvider(
            api_key=settings.sendgrid_api_key,
            from_email=settings.email_from,
            from_name=settings.sender_name,
            reply_to=settings.reply_to_email,
            base_url=settings.sendgrid_base_url,
            sandbox_mode=settings.sendgrid_sandbox_mode,
            sending_enabled=settings.email_sending_enabled,
        )

    if settings.can_send_email() and settings.email_provider == "smtp":
        return SMTPEmailProvider(
            host=settings.email_smtp_host,
            port=settings.email_smtp_port,
            username=settings.email_smtp_user,
            password=settings.email_smtp_password,
            from_email=settings.email_from,
            reply_to=settings.reply_to_email,
            sending_enabled=settings.email_sending_enabled,
        )

    if _mock_singleton is None:
        _mock_singleton = MockEmailProvider()
    return _mock_singleton


def reset_email_singleton() -> None:
    global _mock_singleton
    _mock_singleton = None
