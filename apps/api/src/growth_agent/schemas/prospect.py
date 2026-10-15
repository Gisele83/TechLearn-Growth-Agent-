"""Schémas prospects, sources et qualification."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from growth_agent.schemas.common import ORMModel


class ProspectSourceOut(ORMModel):
    id: uuid.UUID
    kind: str
    url: str
    title: str
    excerpt: str
    collected_at: datetime
    collected_by: str
    provider: str
    license_note: str


class CriterionOut(BaseModel):
    key: str
    label: str
    points: int
    max_points: int
    status: str
    evidence: list[str] = Field(default_factory=list)
    comment: str = ""


class AssessmentOut(ORMModel):
    id: uuid.UUID
    total_score: int
    max_score: int
    verification_status: str
    confidence: str
    criteria: list[CriterionOut]
    rationale: str
    unknowns: list[str]
    assessed_by: str
    model_reference: str
    created_at: datetime


class ProspectOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    campaign_id: uuid.UUID | None
    organization_name: str
    dedupe_key: str
    website: str | None
    domain: str | None
    country_code: str
    city: str | None
    sector: str | None
    headcount_estimate: int | None
    contact_full_name: str | None
    contact_role: str | None
    contact_email: str | None
    stage: str
    verification_status: str
    legal_basis: str
    score: int | None
    notes: str
    discovered_by: str
    created_at: datetime


class ProspectDetailOut(ProspectOut):
    sources: list[ProspectSourceOut] = Field(default_factory=list)
    latest_assessment: AssessmentOut | None = None


class ProspectManualIn(BaseModel):
    """Import manuel d'un prospect.

    Au moins une source est exigée : un prospect sans provenance ne peut pas
    être qualifié, et ne doit donc pas pouvoir entrer dans le système.
    """

    product_code: str
    organization_name: str = Field(min_length=2, max_length=256)
    campaign_id: uuid.UUID | None = None
    website: str | None = None
    country_code: str = Field(default="FR", min_length=2, max_length=2)
    city: str | None = None
    sector: str | None = None
    headcount_estimate: int | None = Field(default=None, ge=0)
    contact_full_name: str | None = None
    contact_role: str | None = None
    contact_email: EmailStr | None = None
    notes: str = ""
    sources: list[ManualSourceIn] = Field(min_length=1)


class ManualSourceIn(BaseModel):
    url: str = Field(min_length=8, max_length=1024, pattern=r"^https?://")
    kind: str = "manual_import"
    title: str = ""
    excerpt: str = ""
    license_note: str = ""


class DiscoveryRequest(BaseModel):
    product_code: str
    campaign_id: uuid.UUID | None = None
    icp_id: uuid.UUID | None = None
    limit: int = Field(default=25, ge=1, le=200)


class DiscoveryResponse(BaseModel):
    created: int
    updated: int
    rejected: int
    rejected_details: list[dict[str, str]] = Field(default_factory=list)
    provider: str
    warnings: list[str] = Field(default_factory=list)


class QualifyRequest(BaseModel):
    icp_id: uuid.UUID | None = None
    use_llm: bool = True


ProspectManualIn.model_rebuild()
