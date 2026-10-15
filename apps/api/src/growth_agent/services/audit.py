"""Journal d'audit.

Append-only et sans donnée sensible : chaque entrée dit qui a fait quoi, sur
quel objet et quand. Les charges utiles passent par `safe_log_payload`, qui
masque emails, téléphones et secrets, et par un condensat qui permet de
rapprocher une entrée d'une donnée d'origine sans la stocker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models.ops import AuditEvent
from growth_agent.security.redaction import digest, safe_log_payload


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: uuid.UUID | None = None,
    actor_role: str = "",
    product_code: str = "",
    outcome: str = "success",
    metadata: dict[str, Any] | None = None,
    payload_for_digest: Any = None,
    now: datetime | None = None,
) -> AuditEvent:
    event = AuditEvent(
        occurred_at=now or datetime.now(UTC),
        actor=actor,
        actor_role=actor_role,
        action=action,
        object_type=object_type,
        object_id=object_id,
        product_code=product_code,
        outcome=outcome,
        metadata_json=safe_log_payload(metadata or {}),
        payload_digest=digest(payload_for_digest) if payload_for_digest is not None else "",
    )
    session.add(event)
    session.flush()
    return event


def list_audit_events(
    session: Session,
    *,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    statement = select(AuditEvent).order_by(AuditEvent.occurred_at.desc())
    if object_type:
        statement = statement.where(AuditEvent.object_type == object_type)
    if object_id:
        statement = statement.where(AuditEvent.object_id == object_id)
    if action:
        statement = statement.where(AuditEvent.action == action)
    return list(session.execute(statement.offset(offset).limit(limit)).scalars())
