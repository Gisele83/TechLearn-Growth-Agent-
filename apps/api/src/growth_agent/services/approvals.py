"""Workflow d'approbation humaine.

Point de contrôle unique avant toute action engageante : message sortant,
devis, remise, contrat, activation de licence, modification d'infrastructure.

Deux propriétés tiennent la garantie « aucun envoi non approuvé » :

* l'approbation porte sur un **condensat du contenu**. Si le brouillon est
  modifié après coup, le condensat ne correspond plus et l'envoi est refusé :
  on ne peut pas faire approuver un texte puis en envoyer un autre ;
* un approbateur ne peut pas valider sa propre demande.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.models.enums import ApprovalDecision, ApprovalSubject, DraftStatus
from growth_agent.models.outreach import ApprovalRequest, MessageDraft
from growth_agent.security.redaction import digest
from growth_agent.services.audit import record_audit

DEFAULT_APPROVAL_TTL_HOURS = 72


class ApprovalError(Exception):
    """Transition d'approbation invalide."""


class SelfApprovalError(ApprovalError):
    """L'auteur d'une demande ne peut pas la valider lui-même."""


@dataclass(slots=True)
class ApprovalGate:
    """Verdict du contrôle d'approbation appliqué à un brouillon."""

    allowed: bool
    reason: str = ""
    approval_id: uuid.UUID | None = None


def compute_draft_digest(draft: MessageDraft) -> str:
    """Empreinte du contenu approuvable : destinataire, objet, corps, canal.

    Tout changement de l'un de ces champs invalide l'approbation.
    """
    return digest(
        "|".join(
            [
                str(draft.recipient_email or ""),
                draft.subject or "",
                draft.body or "",
                draft.channel or "",
            ]
        ),
        length=64,
    )


def request_approval(
    session: Session,
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    requested_by: str,
    summary: str,
    payload_digest: str = "",
    draft: MessageDraft | None = None,
    product_id: uuid.UUID | None = None,
    ttl_hours: int = DEFAULT_APPROVAL_TTL_HOURS,
    now: datetime | None = None,
) -> ApprovalRequest:
    moment = now or datetime.now(UTC)

    if draft is not None:
        payload_digest = compute_draft_digest(draft)
        product_id = product_id or draft.product_id
        draft.status = DraftStatus.PENDING_APPROVAL

    approval = ApprovalRequest(
        subject_type=subject_type,
        subject_id=subject_id,
        draft_id=draft.id if draft is not None else None,
        product_id=product_id,
        requested_by=requested_by,
        summary=summary,
        payload_digest=payload_digest,
        decision=ApprovalDecision.PENDING,
        expires_at=moment + timedelta(hours=ttl_hours),
    )
    session.add(approval)
    session.flush()

    record_audit(
        session,
        actor=requested_by,
        action="approval.requested",
        object_type=subject_type,
        object_id=subject_id,
        metadata={"approval_id": str(approval.id), "summary": summary},
        now=moment,
    )
    return approval


def decide_approval(
    session: Session,
    *,
    approval_id: uuid.UUID,
    decided_by: str,
    approve: bool,
    comment: str = "",
    decider_role: str = "",
    now: datetime | None = None,
) -> ApprovalRequest:
    moment = now or datetime.now(UTC)
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise ApprovalError("Demande d'approbation introuvable.")
    if approval.decision != ApprovalDecision.PENDING:
        raise ApprovalError(
            f"Demande déjà traitée (décision « {approval.decision} ») : "
            "elle ne peut pas être rejugée."
        )
    if approval.expires_at and approval.expires_at < moment:
        approval.decision = ApprovalDecision.EXPIRED
        session.flush()
        raise ApprovalError("Demande expirée : produire un nouveau brouillon.")
    if _same_person(approval.requested_by, decided_by):
        raise SelfApprovalError(
            "L'auteur d'une demande ne peut pas l'approuver : un second regard est requis."
        )

    approval.decision = ApprovalDecision.APPROVED if approve else ApprovalDecision.REJECTED
    approval.decided_by = decided_by
    approval.decided_at = moment
    approval.decision_comment = comment

    if approval.draft_id:
        draft = session.get(MessageDraft, approval.draft_id)
        if draft is not None:
            if approve:
                # Le contenu a-t-il changé depuis la demande ?
                if compute_draft_digest(draft) != approval.payload_digest:
                    approval.decision = ApprovalDecision.REJECTED
                    approval.decision_comment = (
                        "Rejet automatique : le brouillon a été modifié après la demande."
                    )
                    draft.status = DraftStatus.DRAFT
                    session.flush()
                    record_audit(
                        session,
                        actor=decided_by,
                        actor_role=decider_role,
                        action="approval.rejected_content_changed",
                        object_type="message_draft",
                        object_id=draft.id,
                        outcome="blocked",
                        now=moment,
                    )
                    raise ApprovalError(
                        "Le brouillon a changé depuis la demande : approbation annulée."
                    )
                draft.status = DraftStatus.APPROVED
            else:
                draft.status = DraftStatus.REJECTED

    session.flush()
    record_audit(
        session,
        actor=decided_by,
        actor_role=decider_role,
        action="approval.approved" if approve else "approval.rejected",
        object_type=approval.subject_type,
        object_id=approval.subject_id,
        metadata={"approval_id": str(approval.id), "comment": comment},
        now=moment,
    )
    return approval


def check_send_gate(
    session: Session, *, draft: MessageDraft, now: datetime | None = None
) -> ApprovalGate:
    """Un brouillon est-il envoyable ?

    Exige une approbation valide, non expirée, et dont le condensat correspond
    encore au contenu actuel.
    """
    moment = now or datetime.now(UTC)

    if draft.status == DraftStatus.SENT:
        return ApprovalGate(allowed=False, reason="Ce brouillon a déjà été envoyé.")
    if draft.status not in {DraftStatus.APPROVED, DraftStatus.PENDING_APPROVAL}:
        return ApprovalGate(
            allowed=False,
            reason=f"Statut « {draft.status} » : une approbation est requise avant envoi.",
        )

    approval = (
        session.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.draft_id == draft.id,
                ApprovalRequest.decision == ApprovalDecision.APPROVED,
            )
            .order_by(ApprovalRequest.decided_at.desc())
        )
        .scalars()
        .first()
    )

    if approval is None:
        return ApprovalGate(
            allowed=False, reason="Aucune approbation enregistrée pour ce brouillon."
        )
    if approval.expires_at and approval.expires_at < moment:
        return ApprovalGate(
            allowed=False, reason="L'approbation a expiré.", approval_id=approval.id
        )
    if compute_draft_digest(draft) != approval.payload_digest:
        return ApprovalGate(
            allowed=False,
            reason="Le contenu diffère de celui qui a été approuvé.",
            approval_id=approval.id,
        )
    return ApprovalGate(allowed=True, approval_id=approval.id)


def list_pending_approvals(
    session: Session, *, product_id: uuid.UUID | None = None, limit: int = 100
) -> list[ApprovalRequest]:
    statement = (
        select(ApprovalRequest)
        .where(ApprovalRequest.decision == ApprovalDecision.PENDING)
        .order_by(ApprovalRequest.created_at.asc())
        .limit(limit)
    )
    if product_id is not None:
        statement = statement.where(ApprovalRequest.product_id == product_id)
    return list(session.execute(statement).scalars())


def expire_stale_approvals(session: Session, *, now: datetime | None = None) -> int:
    """Passe à `expired` les demandes dont le délai est écoulé."""
    moment = now or datetime.now(UTC)
    stale = session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.decision == ApprovalDecision.PENDING,
            ApprovalRequest.expires_at.is_not(None),
            ApprovalRequest.expires_at < moment,
        )
    ).scalars()
    count = 0
    for approval in stale:
        approval.decision = ApprovalDecision.EXPIRED
        count += 1
    if count:
        session.flush()
    return count


def _same_person(left: str, right: str) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


REQUIRES_APPROVAL: frozenset[str] = frozenset(
    {
        ApprovalSubject.MESSAGE,
        ApprovalSubject.QUOTE,
        ApprovalSubject.DISCOUNT,
        ApprovalSubject.CONTRACT,
        ApprovalSubject.LICENSE_ACTIVATION,
        ApprovalSubject.INFRASTRUCTURE_CHANGE,
    }
)
