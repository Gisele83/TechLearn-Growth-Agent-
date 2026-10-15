"""Workflow d'approbation : rien d'engageant ne part sans second regard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from growth_agent.models.enums import ApprovalDecision, ApprovalSubject, DraftStatus
from growth_agent.models.outreach import MessageDraft
from growth_agent.services.approvals import (
    ApprovalError,
    SelfApprovalError,
    check_send_gate,
    compute_draft_digest,
    decide_approval,
    expire_stale_approvals,
    request_approval,
)


@pytest.fixture
def draft(session, product_educore, make_prospect) -> MessageDraft:
    prospect = make_prospect(product=product_educore)
    item = MessageDraft(
        product_id=product_educore.id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject="Objet initial",
        body="Corps initial. Répondez STOP pour ne plus être contacté.",
    )
    session.add(item)
    session.flush()
    return item


def test_draft_without_approval_cannot_be_sent(session, draft):
    gate = check_send_gate(session, draft=draft)
    assert gate.allowed is False
    assert "approbation" in gate.reason.lower()


def test_pending_approval_is_not_enough(session, draft):
    request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="a@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    assert draft.status == DraftStatus.PENDING_APPROVAL
    assert check_send_gate(session, draft=draft).allowed is False


def test_author_cannot_approve_their_own_request(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    with pytest.raises(SelfApprovalError):
        decide_approval(
            session,
            approval_id=approval.id,
            decided_by="Auteur@Exemple.Example.ORG",  # même personne, autre casse
            approve=True,
        )


def test_approved_draft_can_be_sent(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
    )
    assert draft.status == DraftStatus.APPROVED
    assert check_send_gate(session, draft=draft).allowed is True


def test_editing_the_body_invalidates_the_approval(session, draft):
    """On ne peut pas faire approuver un texte puis en envoyer un autre."""
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
    )
    assert check_send_gate(session, draft=draft).allowed is True

    draft.body = "Corps totalement différent, écrit après la validation."
    session.flush()

    gate = check_send_gate(session, draft=draft)
    assert gate.allowed is False
    assert "diffère de celui qui a été approuvé" in gate.reason


def test_changing_the_recipient_also_invalidates_the_approval(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
    )
    draft.recipient_email = "quelquun.dautre@exemple.example.org"
    session.flush()
    assert check_send_gate(session, draft=draft).allowed is False


def test_approving_a_modified_draft_is_refused(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    draft.body = "Modifié après la demande d'approbation."
    session.flush()
    with pytest.raises(ApprovalError, match="changé depuis la demande"):
        decide_approval(
            session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
        )
    assert draft.status == DraftStatus.DRAFT


def test_rejection_marks_the_draft_rejected(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session,
        approval_id=approval.id,
        decided_by="autre@exemple.example.org",
        approve=False,
        comment="Ton trop commercial.",
    )
    assert draft.status == DraftStatus.REJECTED
    assert check_send_gate(session, draft=draft).allowed is False


def test_a_decision_cannot_be_revisited(session, draft):
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=False
    )
    with pytest.raises(ApprovalError, match="déjà traitée"):
        decide_approval(
            session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
        )


def test_expired_approval_cannot_be_decided_nor_used(session, draft):
    past = datetime.now(UTC) - timedelta(hours=100)
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
        ttl_hours=1,
        now=past,
    )
    with pytest.raises(ApprovalError, match="expirée"):
        decide_approval(
            session, approval_id=approval.id, decided_by="autre@exemple.example.org", approve=True
        )
    assert approval.decision == ApprovalDecision.EXPIRED


def test_expire_stale_approvals_sweeps_old_requests(session, draft):
    request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
        ttl_hours=1,
        now=datetime.now(UTC) - timedelta(hours=48),
    )
    assert expire_stale_approvals(session) == 1
    assert expire_stale_approvals(session) == 0


def test_digest_changes_with_content(session, draft):
    before = compute_draft_digest(draft)
    draft.subject = "Nouvel objet"
    assert compute_draft_digest(draft) != before
