"""Produits, profils cibles et campagnes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from growth_agent.schemas.auth import CurrentUser
from growth_agent.api.deps import CurrentUserDep, SessionDep, require, resolve_product
from growth_agent.models.catalog import Campaign, IdealCustomerProfile, Product
from growth_agent.schemas.catalog import (
    CampaignIn,
    CampaignOut,
    CampaignStatusUpdate,
    ICPIn,
    ICPOut,
    ProductOut,
)
from growth_agent.security.rbac import CAN_ADMINISTER, CAN_OPERATE, CAN_READ
from growth_agent.services.audit import record_audit

router = APIRouter(tags=["catalogue"])


@router.get("/products", response_model=list[ProductOut])
def list_products(session: SessionDep, _: CurrentUser = Depends(require(CAN_READ))):
    return list(session.execute(select(Product).order_by(Product.code)).scalars())


@router.get("/icps", response_model=list[ICPOut])
def list_icps(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    statement = select(IdealCustomerProfile).order_by(IdealCustomerProfile.name)
    if product_code:
        statement = statement.where(
            IdealCustomerProfile.product_id == resolve_product(session, product_code).id
        )
    return list(session.execute(statement).scalars())


@router.post("/icps", response_model=ICPOut, status_code=status.HTTP_201_CREATED)
def create_icp(
    payload: ICPIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_ADMINISTER)),
):
    product = resolve_product(session, payload.product_code)
    if (
        payload.min_headcount is not None
        and payload.max_headcount is not None
        and payload.min_headcount > payload.max_headcount
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="L'effectif minimum ne peut pas dépasser l'effectif maximum.",
        )
    icp = IdealCustomerProfile(
        product_id=product.id,
        name=payload.name,
        description=payload.description,
        country_codes=[c.upper() for c in payload.country_codes],
        target_sectors=payload.target_sectors,
        target_roles=payload.target_roles,
        min_headcount=payload.min_headcount,
        max_headcount=payload.max_headcount,
        need_signals=payload.need_signals,
        exclusion_signals=payload.exclusion_signals,
    )
    session.add(icp)
    session.flush()
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="icp.created",
        object_type="ideal_customer_profile",
        object_id=icp.id,
        product_code=product.code,
    )
    return icp


@router.get("/campaigns", response_model=list[CampaignOut])
def list_campaigns(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    statement = select(Campaign).order_by(Campaign.created_at.desc())
    if product_code:
        statement = statement.where(
            Campaign.product_id == resolve_product(session, product_code).id
        )
    return list(session.execute(statement).scalars())


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    product = resolve_product(session, payload.product_code)
    if payload.icp_id is not None:
        icp = session.get(IdealCustomerProfile, payload.icp_id)
        # Un profil cible ne peut pas franchir la frontière entre produits.
        if icp is None or icp.product_id != product.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Le profil cible n'appartient pas à ce produit.",
            )
    campaign = Campaign(
        product_id=product.id,
        icp_id=payload.icp_id,
        slug=payload.slug,
        name=payload.name,
        objective=payload.objective,
        legal_regime=payload.legal_regime.upper(),
        owner_email=payload.owner_email,
        target_metrics=payload.target_metrics,
    )
    session.add(campaign)
    session.flush()
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="campaign.created",
        object_type="campaign",
        object_id=campaign.id,
        product_code=product.code,
    )
    return campaign


@router.patch("/campaigns/{campaign_id}/status", response_model=CampaignOut)
def update_campaign_status(
    campaign_id: uuid.UUID,
    payload: CampaignStatusUpdate,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne inconnue.")
    previous = campaign.status
    campaign.status = payload.status
    session.flush()
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="campaign.status_changed",
        object_type="campaign",
        object_id=campaign.id,
        metadata={"from": previous, "to": payload.status},
    )
    return campaign
