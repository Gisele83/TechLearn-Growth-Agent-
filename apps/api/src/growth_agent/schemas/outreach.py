"""Schémas brouillons, approbations, envois et opposition."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from growth_agent.models.enums import SuppressionReason, SuppressionScope
from growth_agent.schemas.common import ORMModel


class TemplateIn(BaseModel):
    product_code: str
    slug: str = Field(min_length=2, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    channel: str = "email"
    language: str = "fr"
    subject_template: str = ""
    body_template: str = Field(min_length=10)
    allowed_variables: list[str] = Field(default_factory=list)


class TemplateOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    slug: str
    name: str
    channel: str
    language: str
    subject_template: str
    body_template: str
    allowed_variables: list[str]
    is_active: bool


class ComposeRequest(BaseModel):
    prospect_id: uuid.UUID
    campaign_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None


class DraftOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    campaign_id: uuid.UUID | None
    prospect_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    recipient_email: str | None
    subject: str
    body: str
    language: str
    status: str
    variables_used: dict
    evidence_refs: list[str]
    generated_by: str
    model_reference: str
    blocked_reason: str
    sent_at: datetime | None
    created_at: datetime


class DraftEditIn(BaseModel):
    """Modification d'un brouillon.

    Toute modification invalide l'approbation éventuelle : le condensat du
    contenu change, et le contrôle d'envoi le détecte.
    """

    subject: str | None = Field(default=None, max_length=512)
    body: str | None = None


class ApprovalRequestIn(BaseModel):
    draft_id: uuid.UUID
    summary: str = ""


class ApprovalDecisionIn(BaseModel):
    approve: bool
    comment: str = Field(default="", max_length=2000)


class ApprovalOut(ORMModel):
    id: uuid.UUID
    subject_type: str
    subject_id: uuid.UUID
    draft_id: uuid.UUID | None
    product_id: uuid.UUID | None
    requested_by: str
    summary: str
    decision: str
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str
    expires_at: datetime | None
    created_at: datetime


class SendResponse(BaseModel):
    sent: bool
    simulated: bool
    reason_code: str = ""
    detail: str = ""
    message_id: str = ""


class SuppressionIn(BaseModel):
    value: str = Field(min_length=3, max_length=256)
    reason: SuppressionReason = SuppressionReason.OPT_OUT
    scope: SuppressionScope = SuppressionScope.GLOBAL
    product_code: str | None = None
    note: str = Field(default="", max_length=2000)


class SuppressionOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    scope: str
    value_type: str
    normalized_value: str
    reason: str
    source: str
    note: str
    effective_from: datetime
    created_at: datetime


class ReplyIn(BaseModel):
    prospect_id: uuid.UUID
    reply_text: str = Field(min_length=1, max_length=20000)


class ReplyAnalysisOut(BaseModel):
    intent: str
    sentiment: str
    confidence: str
    requires_human: bool
    opted_out: bool
    objections: list[str] = Field(default_factory=list)
    suggested_next_step: str = ""
    warnings: list[str] = Field(default_factory=list)
