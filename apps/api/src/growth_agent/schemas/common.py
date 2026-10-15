"""Schémas transverses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + self.limit < self.total


class PagedResponse(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    meta: PageMeta


class ErrorResponse(BaseModel):
    """Réponse d'erreur uniforme.

    `detail` est destiné à l'utilisateur et ne contient jamais de trace ni de
    valeur sensible ; `code` permet à l'interface de réagir sans analyser le
    texte.
    """

    code: str
    detail: str
    hint: str = ""


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    database: str
    connectors: dict[str, str]
    warnings: list[str] = Field(default_factory=list)


class AuditEventOut(ORMModel):
    id: uuid.UUID
    occurred_at: datetime
    actor: str
    actor_role: str
    action: str
    object_type: str
    object_id: uuid.UUID | None
    product_code: str
    outcome: str
    metadata_json: dict = Field(default_factory=dict)
