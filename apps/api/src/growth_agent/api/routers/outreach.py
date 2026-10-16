"""Brouillons, approbations, envois, opposition et réponses."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from growth_agent.agents.base import AgentContext
from growth_agent.agents.conversation_analyst import ConversationAnalyst
from growth_agent.agents.outreach_composer import OutreachComposerAgent
from growth_agent.api.deps import (
    EmailDep,
    LLMDep,
    SessionDep,
    SettingsDep,
    require,
    resolve_product,
)
from growth_agent.api.status_codes import UNPROCESSABLE_CONTENT
from growth_agent.models.catalog import Campaign, Product
from growth_agent.models.enums import ApprovalSubject, DraftStatus, ProspectStage
from growth_agent.models.outreach import (
    MessageDraft,
    MessageTemplate,
    SuppressionEntry,
)
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.schemas.auth import CurrentUser
from growth_agent.schemas.outreach import (
    ApprovalDecisionIn,
    ApprovalOut,
    ApprovalRequestIn,
    ComposeRequest,
    DraftEditIn,
    DraftOut,
    ReplyAnalysisOut,
    ReplyIn,
    SendResponse,
    StyleReportOut,
    SuppressionIn,
    SuppressionOut,
    TemplateIn,
    TemplateOut,
)
from growth_agent.security.rbac import CAN_ADMINISTER, CAN_APPROVE, CAN_OPERATE, CAN_READ
from growth_agent.services.approvals import (
    decide_approval,
    list_pending_approvals,
    request_approval,
)
from growth_agent.services.audit import record_audit
from growth_agent.services.outreach import send_draft
from growth_agent.services.suppression import add_suppression, list_suppressions
from growth_agent.services.writing import analyze_message

router = APIRouter(tags=["prospection"])


# --- Gabarits ----------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    statement = select(MessageTemplate).order_by(MessageTemplate.slug)
    if product_code:
        statement = statement.where(
            MessageTemplate.product_id == resolve_product(session, product_code).id
        )
    return list(session.execute(statement).scalars())


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_ADMINISTER)),
):
    product = resolve_product(session, payload.product_code)
    template = MessageTemplate(
        product_id=product.id,
        slug=payload.slug,
        name=payload.name,
        channel=payload.channel,
        language=payload.language,
        subject_template=payload.subject_template,
        body_template=payload.body_template,
        allowed_variables=payload.allowed_variables,
    )
    session.add(template)
    session.flush()
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="template.created",
        object_type="message_template",
        object_id=template.id,
        product_code=product.code,
    )
    return template


# --- Brouillons --------------------------------------------------------------


@router.get("/drafts", response_model=list[DraftOut])
def list_drafts(
    session: SessionDep,
    status_filter: DraftStatus | None = Query(default=None, alias="status"),
    product_code: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(require(CAN_READ)),
):
    statement = select(MessageDraft).order_by(MessageDraft.created_at.desc()).limit(limit)
    if status_filter:
        statement = statement.where(MessageDraft.status == status_filter)
    if product_code:
        statement = statement.where(
            MessageDraft.product_id == resolve_product(session, product_code).id
        )
    return [_with_style(session, d) for d in session.execute(statement).scalars()]


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(
    draft_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    return _with_style(session, _require_draft(session, draft_id))


@router.post("/drafts", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
def compose_draft(
    payload: ComposeRequest,
    session: SessionDep,
    llm: LLMDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    prospect = session.get(ProspectCandidate, payload.prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect inconnu.")
    product = session.get(Product, prospect.product_id)
    campaign = (
        session.get(Campaign, payload.campaign_id)
        if payload.campaign_id
        else (session.get(Campaign, prospect.campaign_id) if prospect.campaign_id else None)
    )
    template = session.get(MessageTemplate, payload.template_id) if payload.template_id else None

    agent = OutreachComposerAgent(llm)
    result = agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        prospect=prospect,
        product=product,
        campaign=campaign,
        template=template,
    )
    draft = result.value.draft
    if draft is None:  # pragma: no cover - l'agent lève sinon
        raise HTTPException(
            status_code=UNPROCESSABLE_CONTENT,
            detail="Aucun brouillon n'a pu être produit.",
        )
    return _with_style(session, draft)


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def edit_draft(
    draft_id: uuid.UUID,
    payload: DraftEditIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    """Modifier un brouillon.

    Un brouillon déjà envoyé n'est plus modifiable. Une modification après
    approbation remet le brouillon à l'état « brouillon » : l'approbation
    portait sur l'ancien texte.
    """
    draft = _require_draft(session, draft_id)
    if draft.status == DraftStatus.SENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce message a déjà été envoyé : il n'est plus modifiable.",
        )
    if payload.subject is not None:
        draft.subject = payload.subject
    if payload.body is not None:
        draft.body = payload.body

    # Modifier un brouillon le ramène à l'état « brouillon » : l'approbation
    # éventuelle portait sur l'ancien texte, et corriger un contenu bloqué est
    # précisément la façon de le débloquer. Les garde-fous d'envoi sont de
    # toute façon réévalués au moment de l'envoi.
    if draft.status in {
        DraftStatus.APPROVED,
        DraftStatus.PENDING_APPROVAL,
        DraftStatus.BLOCKED,
        DraftStatus.REJECTED,
    }:
        draft.status = DraftStatus.DRAFT
        draft.blocked_reason = ""
    session.flush()
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="draft.edited",
        object_type="message_draft",
        object_id=draft.id,
        metadata={"approval_invalidated": True},
    )
    return _with_style(session, draft)


@router.post("/drafts/{draft_id}/send", response_model=SendResponse)
def send(
    draft_id: uuid.UUID,
    session: SessionDep,
    email: EmailDep,
    settings: SettingsDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    draft = _require_draft(session, draft_id)
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor=user.email,
        actor_role=user.role,
        settings=settings,
    )
    if outcome.sent:
        prospect = session.get(ProspectCandidate, draft.prospect_id)
        if prospect is not None and prospect.stage in {
            ProspectStage.DISCOVERED,
            ProspectStage.QUALIFIED,
        }:
            prospect.stage = ProspectStage.CONTACTED
            session.flush()
    return SendResponse(
        sent=outcome.sent,
        simulated=outcome.simulated,
        reason_code=outcome.reason_code,
        detail=outcome.detail,
        message_id=outcome.message_id,
    )


# --- Approbations ------------------------------------------------------------


@router.get("/approvals", response_model=list[ApprovalOut])
def list_approvals(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    product_id = resolve_product(session, product_code).id if product_code else None
    return list_pending_approvals(session, product_id=product_id)


@router.post("/approvals", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def create_approval(
    payload: ApprovalRequestIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    draft = _require_draft(session, payload.draft_id)
    if draft.status == DraftStatus.BLOCKED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Brouillon bloqué : {draft.blocked_reason} "
                "Corriger le message avant de demander une validation."
            ),
        )
    if draft.status == DraftStatus.SENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ce message a déjà été envoyé."
        )
    return request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by=user.email,
        summary=payload.summary or f"Message vers {draft.recipient_email}",
        draft=draft,
    )


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
def decide(
    approval_id: uuid.UUID,
    payload: ApprovalDecisionIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_APPROVE)),
):
    return decide_approval(
        session,
        approval_id=approval_id,
        decided_by=user.email,
        decider_role=user.role,
        approve=payload.approve,
        comment=payload.comment,
    )


# --- Opposition --------------------------------------------------------------


@router.get("/suppressions", response_model=list[SuppressionOut])
def get_suppressions(
    session: SessionDep,
    product_code: str | None = None,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    product_id = resolve_product(session, product_code).id if product_code else None
    return list_suppressions(session, product_id=product_id)


@router.post("/suppressions", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def create_suppression(
    payload: SuppressionIn,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    product_id = resolve_product(session, payload.product_code).id if payload.product_code else None
    entry = add_suppression(
        session,
        value=payload.value,
        reason=payload.reason,
        scope=payload.scope,
        product_id=product_id,
        source=f"api:{user.email}",
        note=payload.note,
    )
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="suppression.added",
        object_type="suppression_entry",
        object_id=entry.id,
        metadata={"scope": entry.scope, "reason": entry.reason},
    )
    return entry


@router.delete("/suppressions/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suppression(
    entry_id: uuid.UUID,
    session: SessionDep,
    user: CurrentUser = Depends(require(CAN_ADMINISTER)),
) -> None:
    """Retirer une opposition. Réservé aux administrateurs et journalisé.

    Ce n'est pas une opération de confort : retirer une opposition rouvre la
    possibilité de contacter quelqu'un qui l'avait refusée.
    """
    entry = session.get(SuppressionEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrée inconnue.")
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="suppression.removed",
        object_type="suppression_entry",
        object_id=entry.id,
        outcome="success",
        metadata={"scope": entry.scope, "reason": entry.reason},
    )
    session.delete(entry)
    session.flush()


# --- Réponses ----------------------------------------------------------------


@router.post("/replies/analyze", response_model=ReplyAnalysisOut)
def analyze_reply(
    payload: ReplyIn,
    session: SessionDep,
    llm: LLMDep,
    user: CurrentUser = Depends(require(CAN_OPERATE)),
):
    prospect = session.get(ProspectCandidate, payload.prospect_id)
    if prospect is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect inconnu.")

    agent = ConversationAnalyst(llm)
    result = agent.execute(
        AgentContext(session=session, actor=user.email, actor_role=user.role),
        prospect=prospect,
        reply_text=payload.reply_text,
    )
    outcome = result.value
    return ReplyAnalysisOut(
        intent=outcome.classification.intent,
        sentiment=outcome.classification.sentiment,
        confidence=outcome.classification.confidence,
        requires_human=outcome.requires_human,
        opted_out=outcome.opted_out,
        objections=outcome.classification.objections,
        suggested_next_step=outcome.classification.suggested_next_step,
        warnings=result.warnings,
    )


def _with_style(session, draft: MessageDraft) -> DraftOut:
    """Joint au brouillon son rapport de naturel.

    Recalculé à chaque lecture plutôt que stocké : le texte peut être modifié
    après coup, et un score figé mentirait sur le contenu réel.
    """
    out = DraftOut.model_validate(draft)
    product = session.get(Product, draft.product_id)
    prospect = session.get(ProspectCandidate, draft.prospect_id)
    report = analyze_message(
        draft.body,
        subject=draft.subject,
        organization_name=prospect.organization_name if prospect else "",
        product_name=product.name if product else "",
    )
    out.style = StyleReportOut(**report.as_dict())
    return out


def _require_draft(session, draft_id: uuid.UUID) -> MessageDraft:
    draft = session.get(MessageDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brouillon inconnu.")
    return draft
