"""Types SQLAlchemy portables entre PostgreSQL (cible) et SQLite (tests rapides).

PostgreSQL reste la base de référence : les colonnes utilisent ses types natifs
(`UUID`, `JSONB`) dès que le dialecte est `postgresql`. Sous SQLite, elles se
replient sur des équivalents textuels afin que la même suite de tests puisse
s'exécuter sans serveur.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CHAR, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """UUID natif sous PostgreSQL, CHAR(36) ailleurs."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value: Any, dialect: Any) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class JSONDict(TypeDecorator):
    """JSONB sous PostgreSQL, texte JSON ailleurs."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        return json.loads(value)


class UTCDateTime(TypeDecorator):
    """Horodatage toujours rendu en UTC *avec* fuseau.

    PostgreSQL restitue des datetimes conscients du fuseau, SQLite non. Sans
    uniformisation, une comparaison de dates réussit en production et échoue
    en test (ou l'inverse), ce qui est la pire des situations. Ce type force
    donc l'UTC à l'écriture comme à la lecture, quel que soit le moteur.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            # Une date naïve est interprétée comme UTC : c'est la convention
            # de l'application, rappelée dans `db.base.utcnow`.
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
