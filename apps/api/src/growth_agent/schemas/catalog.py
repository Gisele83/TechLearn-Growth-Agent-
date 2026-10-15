"""Schémas produits, campagnes et profils cibles."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from growth_agent.models.enums import CampaignStatus
from growth_agent.schemas.common import ORMModel


class ProductOut(ORMModel):
    id: uuid.UUID
    code: str
    name: str
    sector: str
    description: str


class ICPIn(BaseModel):
    product_code: str
    name: str = Field(min_length=2, max_length=128)
    description: str = ""
    country_codes: list[str] = Field(default_factory=list)
    target_sectors: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    min_headcount: int | None = Field(default=None, ge=0)
    max_headcount: int | None = Field(default=None, ge=0)
    need_signals: list[str] = Field(default_factory=list)
    exclusion_signals: list[str] = Field(default_factory=list)


class ICPOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    description: str
    country_codes: list[str]
    target_sectors: list[str]
    target_roles: list[str]
    min_headcount: int | None
    max_headcount: int | None
    need_signals: list[str]
    exclusion_signals: list[str]


class CampaignIn(BaseModel):
    product_code: str
    slug: str = Field(min_length=2, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=2, max_length=192)
    objective: str = ""
    icp_id: uuid.UUID | None = None
    legal_regime: str = "FR"
    owner_email: str = ""
    target_metrics: dict = Field(default_factory=dict)


class CampaignOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    icp_id: uuid.UUID | None
    slug: str
    name: str
    objective: str
    status: str
    legal_regime: str
    owner_email: str
    target_metrics: dict
    starts_at: datetime | None
    ends_at: datetime | None
    created_at: datetime


class CampaignStatusUpdate(BaseModel):
    status: CampaignStatus
