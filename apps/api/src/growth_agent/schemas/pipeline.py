"""Schémas du pipeline commercial."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from growth_agent.models.enums import (
    LossReason,
    NextActionKind,
    NextActionStatus,
    OpportunityStage,
)
from growth_agent.schemas.common import ORMModel


class OpportunityOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    prospect_id: uuid.UUID | None
    organization_name: str
    stage: str
    owner_email: str
    amount: int | None
    currency: str
    expected_close_on: datetime | None
    closed_at: datetime | None
    loss_reason: str
    notes: str


class OpportunityIn(BaseModel):
    prospect_id: uuid.UUID
    owner_email: str = Field(min_length=3, max_length=256)
    amount: int | None = Field(default=None, ge=0)
    expected_close_on: datetime | None = None


class StageIn(BaseModel):
    stage: OpportunityStage
    #: Exigé pour une perte : « perdu » sans motif n'apprend rien.
    loss_reason: LossReason | None = None
    note: str = Field(default="", max_length=2000)


class DemoReportIn(BaseModel):
    held: bool
    summary: str = Field(default="", max_length=4000)
    #: Catégories, pas verbatims : aucun propos de personne n'entre ici.
    objections: list[str] = Field(default_factory=list, max_length=10)
    next_step: str = Field(default="", max_length=192)
    follow_up_in_days: int = Field(default=3, ge=1, le=90)


class NextActionOut(ORMModel):
    id: uuid.UUID
    opportunity_id: uuid.UUID | None
    product_id: uuid.UUID
    prospect_id: uuid.UUID | None
    kind: str
    title: str
    owner_email: str
    due_at: datetime
    status: str
    completed_at: datetime | None
    outcome: str
    crm_remote_id: str


class NextActionIn(BaseModel):
    kind: NextActionKind = NextActionKind.FOLLOW_UP
    title: str = Field(min_length=3, max_length=192)
    owner_email: str = Field(min_length=3, max_length=256)
    due_at: datetime
    opportunity_id: uuid.UUID | None = None
    prospect_id: uuid.UUID | None = None
    product_code: str = ""


class ActionCompletionIn(BaseModel):
    outcome: str = Field(default="", max_length=2000)
    cancelled: bool = False


class WorkloadOut(BaseModel):
    owner_email: str
    overdue: int
    today: int
    upcoming: int


__all__ = [
    "ActionCompletionIn",
    "DemoReportIn",
    "NextActionIn",
    "NextActionOut",
    "NextActionStatus",
    "OpportunityIn",
    "OpportunityOut",
    "StageIn",
    "WorkloadOut",
]
