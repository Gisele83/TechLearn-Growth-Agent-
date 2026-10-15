"""Schémas des indicateurs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FunnelOut(BaseModel):
    discovered: int
    qualified: int
    contacted: int
    replied: int
    demos_scheduled: int
    demos_held: int
    pilots_started: int
    won: int
    lost: int
    opted_out: int
    blocked: int


class ProductKPIOut(BaseModel):
    product_code: str
    window_days: int
    computed_at: datetime
    window_start: datetime
    funnel: FunnelOut
    #: `null` quand le dénominateur est nul — l'absence de donnée n'est pas 0 %.
    reply_rate: float | None = None
    demo_rate: float | None = None
    pilot_rate: float | None = None
    qualified_organizations: int = 0
    verified_organizations: int = 0
    unknown_organizations: int = 0
    notes: list[str] = Field(default_factory=list)


class TargetGapOut(BaseModel):
    product_code: str
    metric: str
    target: int
    actual: int
    gap: int
    attained: bool
    note: str


class GrowthReportOut(BaseModel):
    kpis: list[ProductKPIOut]
    target_gaps: list[TargetGapOut]
    recommendations: list[str]


class TimeseriesPoint(BaseModel):
    date: str
    count: int
