"""Indicateurs, journal d'audit et suivi des pilotes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from growth_agent.agents.base import AgentContext
from growth_agent.agents.customer_success import CustomerSuccessAgent
from growth_agent.agents.growth_analyst import GrowthAnalystAgent
from growth_agent.api.deps import (
    SessionDep,
    SettingsDep,
    require,
    resolve_product,
)
from growth_agent.connectors.product.factory import get_product_adapter
from growth_agent.models.enums import OutreachEventType
from growth_agent.schemas.analytics import (
    GrowthReportOut,
    ProductKPIOut,
    TimeseriesPoint,
)
from growth_agent.schemas.auth import CurrentUser
from growth_agent.schemas.common import AuditEventOut
from growth_agent.security.rbac import CAN_OPERATE, CAN_READ
from growth_agent.services.analytics import (
    compute_all_kpis,
    compute_product_kpis,
    funnel_timeseries,
)
from growth_agent.services.audit import list_audit_events

router = APIRouter(tags=["pilotage"])


@router.get("/kpis", response_model=list[ProductKPIOut])
def kpis(
    session: SessionDep,
    product_code: str | None = None,
    window_days: int = Query(default=90, ge=1, le=730),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    """KPI recalculés à la demande depuis les événements."""
    if product_code:
        product = resolve_product(session, product_code)
        return [
            ProductKPIOut(
                **compute_product_kpis(
                    session,
                    product_id=product.id,
                    product_code=product.code,
                    window_days=window_days,
                ).as_dict()
            )
        ]
    return [
        ProductKPIOut(**k.as_dict()) for k in compute_all_kpis(session, window_days=window_days)
    ]


@router.get("/kpis/timeseries", response_model=list[TimeseriesPoint])
def timeseries(
    session: SessionDep,
    product_code: str,
    event_type: OutreachEventType = OutreachEventType.SENT,
    window_days: int = Query(default=30, ge=1, le=365),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    product = resolve_product(session, product_code)
    return [
        TimeseriesPoint(**point)
        for point in funnel_timeseries(
            session,
            product_id=product.id,
            event_type=event_type,
            window_days=window_days,
        )
    ]


@router.get("/report", response_model=GrowthReportOut)
def growth_report(
    session: SessionDep,
    window_days: int = Query(default=90, ge=1, le=730),
    user: CurrentUser = Depends(require(CAN_READ)),
):
    agent = GrowthAnalystAgent()
    result = agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        window_days=window_days,
    )
    return GrowthReportOut(**result.value.as_dict())


@router.get("/audit", response_model=list[AuditEventOut])
def audit(
    session: SessionDep,
    object_type: str | None = None,
    object_id: uuid.UUID | None = None,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    return list_audit_events(
        session,
        object_type=object_type,
        object_id=object_id,
        action=action,
        limit=limit,
        offset=offset,
    )


@router.post("/pilots/{product_code}/sync")
def sync_pilots(
    product_code: str,
    session: SessionDep,
    settings: SettingsDep,
    since_days: int = Query(default=90, ge=1, le=730),
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    """Importe les statuts de pilote depuis la plateforme produit.

    LECTURE SEULE. En l'absence d'autorisation explicite, l'adaptateur mock est
    utilisé et la réponse le signale.
    """
    product = resolve_product(session, product_code)
    adapter = get_product_adapter(product.code, settings)
    agent = CustomerSuccessAgent(adapter)
    result = agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        product=product,
        since=datetime.now(UTC) - timedelta(days=since_days),
    )
    outcome = result.value
    return {
        "product_code": product.code,
        "adapter_mode": adapter.mode,
        "read_only": outcome.read_only,
        "imported": outcome.imported,
        "duplicates": outcome.duplicates,
        "renewals_due": outcome.renewals_due,
        "warnings": result.warnings,
    }
