from growth_agent.connectors.email.base import EmailMessage, EmailProvider, EmailSendResult
from growth_agent.connectors.email.factory import get_email_provider
from growth_agent.connectors.email.mock import MockEmailProvider
from growth_agent.connectors.email.smtp import SMTPEmailProvider

__all__ = [
    "EmailMessage",
    "EmailProvider",
    "EmailSendResult",
    "MockEmailProvider",
    "SMTPEmailProvider",
    "get_email_provider",
]
