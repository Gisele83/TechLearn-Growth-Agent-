"""Journalisation sans donnée personnelle ni secret.

Deux protections superposées, et la seconde est indispensable.

Le **filtre** masque le message de chaque enregistrement. Il ne suffit pas :
`logging.Formatter` rend séparément la trace d'exception, qui ne passe pas par
le message. Or une erreur SQLAlchemy contient l'instruction *et ses
paramètres* — donc potentiellement une adresse email. Un simple
`logger.exception()` suffisait à l'écrire en clair.

Le **formateur** masque donc la chaîne finale, trace comprise. C'est lui qui
porte la garantie ; le filtre reste pour les enregistrements qui partiraient
vers un autre gestionnaire.
"""

from __future__ import annotations

import logging
import sys

from growth_agent.security.redaction import redact_text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - formatage défaillant
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Masque la sortie rendue, trace d'exception et pile incluses.

    `logging.Formatter.format` assemble le message, puis y ajoute
    `exc_text` et `stack_info`. En masquant après cet assemblage, on couvre
    tout ce qui finira réellement dans le journal.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s"))
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Les requêtes SQL peuvent contenir des valeurs : jamais en INFO.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
