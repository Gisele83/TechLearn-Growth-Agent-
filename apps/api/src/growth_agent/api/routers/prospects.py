"""Prospects : découverte, import manuel, consultation, qualification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from growth_agent.schemas.auth import CurrentUser
from growth_agent.agents.base import AgentContext
from growth_agent.agents.lead_intelligence import LeadIntelligenceAgent
from growth_agent.agents.market_scout import MarketScoutAgent
from growth_agent.api.deps import (
    LLMDep,
    SearchDep,
    SessionDep,
    SettingsDep,
    require,
    resolve_product,
)
from growth_agent.models.catalog import Campaign, IdealCustomerProfile
from growth_agent.models.enums import ProspectStage, VerificationStatus
from growth_agent.models.prospect import (
    ProspectCandidate,
    ProspectSource,
    QualificationAssessment,
)
from growth_agent.schemas.common import PagedResponse, PageMeta
from growth_agent.schemas.prospect import (
    AssessmentOut,
    DiscoveryRequest,
    DiscoveryResponse,
    ProspectDetailOut,
    ProspectManualIn,
    ProspectOut,
    ProspectSourceOut,
    QualifyRequest,
)
from growth_agent.security.rbac import CAN_OPERATE, CAN_READ
from growth_agent.services.audit import record_audit
from growth_agent.services.dedupe import build_dedupe_key, normalize_domain, normalize_email

router = APIRouter(prefix="/prospects", tags=["prospects"])


@router.get("", response_model=PagedResponse[ProspectOut])
def list_prospects(
    session: SessionDep,
    product_code: str | None = None,
    stage: ProspectStage | None = None,
    verification_status: VerificationStatus | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    search: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    filters = []
    if product_code:
        filters.append(ProspectCandidate.product_id == resolve_product(session, product_code).id)
    if stage:
        filters.append(ProspectCandidate.stage == stage)
    if verification_status:
        filters.append(ProspectCandidate.verification_status == verification_status)
    if min_score is not None:
        filters.append(ProspectCandidate.score >= min_score)
    if search:
        filters.append(ProspectCandidate.organization_name.ilike(f"%{search}%"))

    total = session.execute(
        select(func.count()).select_from(ProspectCandidate).where(*filters)
    ).scalar_one()
    items = list(
        session.execute(
            select(ProspectCandidate)
            .where(*filters)
            .order_by(
                ProspectCandidate.score.desc().nullslast(),
                ProspectCandidate.created_at.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).scalars()
    )
    return PagedResponse[ProspectOut](
        items=[ProspectOut.model_validate(i) for i in items],
        meta=PageMeta(total=total, limit=limit, offset=offset),
    )


@router.get("/{prospect_id}", response_model=ProspectDetailOut)
def get_prospect(
    prospect_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    prospect = _require_prospect(session, prospect_id)
    sources = list(
        session.execute(
            select(ProspectSource)
            .where(ProspectSource.prospect_id == prospect.id)
            .order_by(ProspectSource.collected_at.desc())
        ).scalars()
    )
    assessment = session.execute(
        select(QualificationAssessment)
        .where(QualificationAssessment.prospect_id == prospect.id)
        .order_by(QualificationAssessment.created_at.desc())
    ).scalars().first()

    detail = ProspectDetailOut.model_validate(prospect)
    detail.sources = [ProspectSourceOut.model_validate(s) for s in sources]
    detail.latest_assessment = AssessmentOut.model_validate(assessment) if assessment else None
    return detail


@router.post("", response_model=ProspectOut, status_code=status.HTTP_201_CREATED)
def create_prospect(
    payload: ProspectManualIn,
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    """Import manuel. Au moins une source est exigée par le schéma."""
    product = resolve_product(session, payload.product_code)
    dedupe_key = build_dedupe_key(
        organization_name=payload.organization_name,
        website=payload.website,
        contact_email=str(payload.contact_email) if payload.contact_email else None,
        country_code=payload.country_code,
    )
    existing = session.execute(
        select(ProspectCandidate).where(
            ProspectCandidate.product_id == product.id,
            ProspectCandidate.dedupe_key == dedupe_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Ce prospect existe déjà pour {product.name} "
                f"(clé de dédoublonnage « {dedupe_key} »)."
            ),
        )

    prospect = ProspectCandidate(
        product_id=product.id,
        campaign_id=payload.campaign_id,
        organization_name=payload.organization_name.strip(),
        dedupe_key=dedupe_key,
        website=payload.website,
        domain=normalize_domain(payload.website),
        country_code=payload.country_code.upper(),
        city=payload.city,
        sector=payload.sector,
        headcount_estimate=payload.headcount_estimate,
        contact_full_name=payload.contact_full_name,
        contact_role=payload.contact_role,
        contact_email=normalize_email(str(payload.contact_email)) if payload.contact_email else None,
        notes=payload.notes,
        discovered_by=f"manual:{user.email}",
        retention_until=None,
    )
    session.add(prospect)
    session.flush()

    now = datetime.now(UTC)
    prospect.retention_until = now + timedelta(days=settings.data_retention_days)
    for source in payload.sources:
        session.add(
            ProspectSource(
                prospect_id=prospect.id,
                kind=source.kind,
                url=source.url,
                title=source.title,
                excerpt=source.excerpt,
                collected_at=now,
                collected_by=f"manual:{user.email}",
                provider="manual",
                license_note=source.license_note,
            )
        )
    session.flush()

    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="prospect.imported",
        object_type="prospect_candidate",
        object_id=prospect.id,
        product_code=product.code,
        metadata={"sources": len(payload.sources)},
    )
    return prospect


@router.post("/discover", response_model=DiscoveryResponse)
def discover(
    payload: DiscoveryRequest,
    session: SessionDep,
    search: SearchDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    product = resolve_product(session, payload.product_code)
    campaign = session.get(Campaign, payload.campaign_id) if payload.campaign_id else None
    icp = _resolve_icp(session, payload.icp_id, product.id, campaign)

    agent = MarketScoutAgent(search)
    result = agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        product=product,
        campaign=campaign,
        icp=icp,
        limit=payload.limit,
    )
    outcome = result.value
    return DiscoveryResponse(
        created=len(outcome.created),
        updated=len(outcome.updated),
        rejected=len(outcome.rejected),
        rejected_details=outcome.rejected,
        provider=search.provider,
        warnings=result.warnings,
    )


@router.post("/{prospect_id}/qualify", response_model=AssessmentOut)
def qualify(
    prospect_id: uuid.UUID,
    payload: QualifyRequest,
    session: SessionDep,
    llm: LLMDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    prospect = _require_prospect(session, prospect_id)
    campaign = session.get(Campaign, prospect.campaign_id) if prospect.campaign_id else None
    icp = _resolve_icp(session, payload.icp_id, prospect.product_id, campaign)

    agent = LeadIntelligenceAgent(llm)
    agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        prospect=prospect,
        icp=icp,
        use_llm=payload.use_llm,
    )
    assessment = session.execute(
        select(QualificationAssessment)
        .where(QualificationAssessment.prospect_id == prospect.id)
        .order_by(QualificationAssessment.created_at.desc())
    ).scalars().first()
    if assessment is None:  # pragma: no cover - l'agent en crée toujours une
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="La qualification n'a produit aucune évaluation.",
        )
    return assessment


@router.get("/{prospect_id}/assessments", response_model=list[AssessmentOut])
def list_assessments(
    prospect_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    _require_prospect(session, prospect_id)
    return list(
        session.execute(
            select(QualificationAssessment)
            .where(QualificationAssessment.prospect_id == prospect_id)
            .order_by(QualificationAssessment.created_at.desc())
        ).scalars()
    )


def _require_prospect(session, prospect_id: uuid.UUID) -> ProspectCandidate:
    prospect = session.get(ProspectCandidate, prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect inconnu.")
    return prospect


def _resolve_icp(
    session,
    icp_id: uuid.UUID | None,
    product_id: uuid.UUID,
    campaign: Campaign | None,
) -> IdealCustomerProfile | None:
    """Profil explicite, sinon celui de la campagne. Toujours du bon produit."""
    resolved_id = icp_id or (campaign.icp_id if campaign else None)
    if resolved_id is None:
        return None
    icp = session.get(IdealCustomerProfile, resolved_id)
    if icp is None or icp.product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Le profil cible n'appartient pas à ce produit.",
        )
    return icp
