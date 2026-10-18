"""Dossiers commerciaux, prochaines actions, lecture par cohorte.

Rien ici ne déclenche d'envoi. Ces routes servent à **suivre** le travail
commercial : ouvrir un dossier, le faire avancer, consigner une
démonstration, et savoir ce qui est en retard.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from growth_agent.api.deps import SessionDep, require, resolve_product
from growth_agent.models.catalog import Campaign
from growth_agent.models.enums import NextActionStatus, OpportunityStage
from growth_agent.models.pipeline import NextAction, Opportunity
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.schemas.auth import CurrentUser
from growth_agent.schemas.pipeline import (
    ActionCompletionIn,
    DemoReportIn,
    NextActionIn,
    NextActionOut,
    OpportunityIn,
    OpportunityOut,
    StageIn,
    WorkloadOut,
)
from growth_agent.security.rbac import CAN_OPERATE, CAN_READ
from growth_agent.services.cohorts import compute_cohorts
from growth_agent.services.pipeline import (
    DemoReport,
    PipelineError,
    advance,
    complete_action,
    open_opportunity,
    overdue_actions,
    record_demo,
    schedule_action,
    workload_by_owner,
)

router = APIRouter(tags=["pipeline"])


def _refused(error: PipelineError) -> HTTPException:
    """Une règle du pipeline n'est pas une erreur serveur : c'est un refus
    motivé, et le motif doit remonter tel quel à l'utilisateur."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


# --- Dossiers ---------------------------------------------------------------


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    session: SessionDep,
    product_code: str | None = None,
    stage: OpportunityStage | None = None,
    owner_email: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    statement = select(Opportunity).order_by(Opportunity.created_at.desc()).limit(limit)
    if product_code:
        statement = statement.where(
            Opportunity.product_id == resolve_product(session, product_code).id
        )
    if stage:
        statement = statement.where(Opportunity.stage == stage)
    if owner_email:
        statement = statement.where(Opportunity.owner_email == owner_email.strip().lower())
    return list(session.execute(statement).scalars())


@router.post("/opportunities", response_model=OpportunityOut, status_code=status.HTTP_201_CREATED)
def create_opportunity(
    payload: OpportunityIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    """Ouvre un dossier. Un prospect qui en a déjà un ouvert renvoie
    celui-ci : deux dossiers, ce sont deux personnes qui relancent sans le
    savoir."""
    prospect = session.get(ProspectCandidate, payload.prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect inconnu.")
    return open_opportunity(
        session,
        prospect=prospect,
        owner_email=payload.owner_email,
        amount=payload.amount,
        expected_close_on=payload.expected_close_on,
        actor=user.email,
        actor_role=user.role,
    )


@router.post("/opportunities/{opportunity_id}/stage", response_model=OpportunityOut)
def move_stage(
    opportunity_id: uuid.UUID,
    payload: StageIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    opportunity = _require_opportunity(session, opportunity_id)
    try:
        return advance(
            session,
            opportunity=opportunity,
            stage=payload.stage,
            loss_reason=payload.loss_reason or "",
            note=payload.note,
            actor=user.email,
            actor_role=user.role,
        )
    except PipelineError as error:
        raise _refused(error) from error


@router.post(
    "/opportunities/{opportunity_id}/demo-report",
    response_model=NextActionOut,
    status_code=status.HTTP_201_CREATED,
)
def report_demo(
    opportunity_id: uuid.UUID,
    payload: DemoReportIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    """Consigne une démonstration et produit la prochaine action.

    Une démonstration sans compte rendu est une démonstration qu'on ne saura
    pas exploiter. Même un rendez-vous manqué appelle une suite.
    """
    opportunity = _require_opportunity(session, opportunity_id)
    try:
        action = record_demo(
            session,
            opportunity=opportunity,
            report=DemoReport(
                held=payload.held,
                summary=payload.summary,
                objections=tuple(payload.objections),
                next_step=payload.next_step,
            ),
            follow_up_in_days=payload.follow_up_in_days,
            actor=user.email,
            actor_role=user.role,
        )
    except PipelineError as error:
        raise _refused(error) from error
    return action


# --- Prochaines actions -----------------------------------------------------


@router.get("/next-actions", response_model=list[NextActionOut])
def list_next_actions(
    session: SessionDep,
    product_code: str | None = None,
    owner_email: str | None = None,
    overdue_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    product_id = resolve_product(session, product_code).id if product_code else None
    if overdue_only:
        return overdue_actions(
            session, product_id=product_id, owner_email=owner_email or "", limit=limit
        )

    statement = (
        select(NextAction)
        .where(NextAction.status == NextActionStatus.OPEN)
        .order_by(NextAction.due_at)
        .limit(limit)
    )
    if product_id is not None:
        statement = statement.where(NextAction.product_id == product_id)
    if owner_email:
        statement = statement.where(NextAction.owner_email == owner_email.strip().lower())
    return list(session.execute(statement).scalars())


@router.post("/next-actions", response_model=NextActionOut, status_code=status.HTTP_201_CREATED)
def create_next_action(
    payload: NextActionIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    opportunity = (
        _require_opportunity(session, payload.opportunity_id) if payload.opportunity_id else None
    )
    product_id = resolve_product(session, payload.product_code).id if payload.product_code else None
    try:
        return schedule_action(
            session,
            opportunity=opportunity,
            product_id=product_id,
            prospect_id=payload.prospect_id,
            kind=payload.kind,
            title=payload.title,
            owner_email=payload.owner_email,
            due_at=payload.due_at,
            actor=user.email,
            actor_role=user.role,
        )
    except PipelineError as error:
        raise _refused(error) from error


@router.post("/next-actions/{action_id}/complete", response_model=NextActionOut)
def finish_action(
    action_id: uuid.UUID,
    payload: ActionCompletionIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    action = session.get(NextAction, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action inconnue.")
    try:
        return complete_action(
            session,
            action=action,
            outcome=payload.outcome,
            cancelled=payload.cancelled,
            actor=user.email,
            actor_role=user.role,
        )
    except PipelineError as error:
        raise _refused(error) from error


@router.get("/next-actions/workload", response_model=list[WorkloadOut])
def workload(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    """Charge par personne. Un total global masque celle qui croule."""
    product_id = resolve_product(session, product_code).id if product_code else None
    return workload_by_owner(session, product_id=product_id)


# --- Lecture par cohorte ----------------------------------------------------


@router.get("/cohorts")
def cohorts(
    session: SessionDep,
    product_code: str,
    campaign_slug: str | None = None,
    months: int = Query(default=6, ge=1, le=36),
    include_simulated: bool = False,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    """Entonnoir par cohorte de départ, délais médians, motifs de perte.

    Par cohorte et non en volumes cumulés : un total mélange des
    organisations contactées il y a six mois avec d'autres contactées hier,
    et ne dit pas si la conversion s'améliore.
    """
    product = resolve_product(session, product_code)
    campaign = None
    if campaign_slug:
        campaign = session.execute(
            select(Campaign).where(
                Campaign.product_id == product.id, Campaign.slug == campaign_slug
            )
        ).scalar_one_or_none()
        if campaign is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campagne inconnue.")
    return compute_cohorts(
        session,
        product_id=product.id,
        product_code=product.code,
        campaign_id=campaign.id if campaign else None,
        campaign_slug=campaign.slug if campaign else "",
        months=months,
        include_simulated=include_simulated,
    ).as_dict()


def _require_opportunity(session, opportunity_id: uuid.UUID) -> Opportunity:
    opportunity = session.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dossier inconnu.")
    return opportunity
