"""Journalisation sans donnée personnelle ni secret.

Un filtre applique `redact_text` au message final de chaque enregistrement.
C'est une protection de dernier recours : le code appelant doit déjà éviter de
journaliser une donnée sensible, mais une erreur d'inattention ne doit pas se
traduire par une adresse email en clair dans les journaux.
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


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s"))
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Les requêtes SQL peuvent contenir des valeurs : jamais en INFO.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
