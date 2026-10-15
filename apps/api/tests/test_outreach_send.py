"""Garde-fous d'envoi : les six contrôles du service d'envoi."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from growth_agent.config import Settings
from growth_agent.models.enums import (
    ApprovalSubject,
    DraftStatus,
    OutreachEventType,
    SuppressionScope,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.outreach import send_draft
from growth_agent.services.suppression import add_suppression


def _make_draft(session, product, prospect, *, subject="Objet", body=None) -> MessageDraft:
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject=subject,
        body=body or "Bonjour. Répondez STOP pour ne plus recevoir nos messages.",
    )
    session.add(draft)
    session.flush()
    return draft


def _approve(session, draft) -> None:
    approval = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=draft.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=draft,
    )
    decide_approval(
        session, approval_id=approval.id, decided_by="valideur@exemple.example.org", approve=True
    )


def _events(session, draft, event_type):
    return (
        session.query(OutreachEvent)
        .filter(OutreachEvent.draft_id == draft.id, OutreachEvent.event_type == event_type)
        .all()
    )


def test_send_without_approval_is_refused(session, product_educore, make_prospect, email, settings):
    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "not_approved"
    assert email.outbox == []
    assert len(_events(session, draft, OutreachEventType.SEND_BLOCKED)) == 1


def test_send_to_suppressed_contact_is_refused(
    session, product_educore, make_prospect, email, settings
):
    prospect = make_prospect(product=product_educore, contact_email="refus@exemple.example.org")
    draft = _make_draft(session, product_educore, prospect)
    _approve(session, draft)
    add_suppression(session, value="refus@exemple.example.org", scope=SuppressionScope.GLOBAL)
    session.flush()

    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "suppressed"
    assert email.outbox == []
    assert draft.status == DraftStatus.BLOCKED


def test_suppression_is_checked_before_approval(
    session, product_educore, make_prospect, email, settings
):
    """L'ordre compte : un contact opposé est écarté même si, par ailleurs,
    l'approbation manque aussi. Le motif rapporté doit être l'opposition."""
    prospect = make_prospect(product=product_educore, contact_email="refus2@exemple.example.org")
    draft = _make_draft(session, product_educore, prospect)
    add_suppression(session, value="refus2@exemple.example.org")
    session.flush()

    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.reason_code == "suppressed"


def test_approved_draft_is_sent_but_simulated(
    session, product_educore, make_prospect, email, settings
):
    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    _approve(session, draft)
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is True
    assert outcome.simulated is True, "sans autorisation d'envoi réel, l'envoi doit être simulé"
    assert len(email.outbox) == 1
    assert draft.status == DraftStatus.SENT
    assert draft.sent_at is not None


def test_sent_message_carries_one_click_unsubscribe_headers(
    session, product_educore, make_prospect, email, settings
):
    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    _approve(session, draft)
    send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    message = email.outbox[0]
    assert "List-Unsubscribe" in message.headers
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_resending_the_same_draft_is_refused(
    session, product_educore, make_prospect, email, settings
):
    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    _approve(session, draft)
    send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "already_sent"
    assert len(email.outbox) == 1


def test_identical_content_to_same_contact_is_deduplicated(
    session, product_educore, make_prospect, email, settings
):
    """Deux brouillons distincts mais au contenu identique ne partent pas deux
    fois : c'est le contenu qui fait le doublon, pas l'identifiant."""
    prospect = make_prospect(product=product_educore)
    first = _make_draft(session, product_educore, prospect)
    _approve(session, first)
    send_draft(
        session,
        draft=first,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )

    second = _make_draft(session, product_educore, prospect)
    _approve(session, second)
    outcome = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "duplicate"
    assert len(email.outbox) == 1


def test_contact_frequency_cap_is_enforced(session, product_educore, make_prospect, email):
    """Au-delà du plafond mensuel par contact, l'envoi est refusé."""
    capped = Settings(email_max_per_contact_per_30_days=1)
    prospect = make_prospect(product=product_educore)

    first = _make_draft(session, product_educore, prospect, subject="Premier")
    _approve(session, first)
    assert (
        send_draft(
            session,
            draft=first,
            email_provider=email,
            actor="moi@exemple.example.org",
            settings=capped,
        ).sent
        is True
    )

    second = _make_draft(session, product_educore, prospect, subject="Deuxième")
    _approve(session, second)
    outcome = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=capped,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "frequency_contact"


def test_frequency_block_is_transient_not_terminal(session, product_educore, make_prospect, email):
    """Un plafond atteint n'est pas une faute du message : le brouillon n'est
    pas condamné et repart une fois la fenêtre écoulée.

    Il faut en revanche une nouvelle approbation : au bout de 31 jours, celle
    d'origine a expiré, ce qui est le comportement voulu — on ne réutilise pas
    une validation vieille d'un mois.
    """
    capped = Settings(email_max_per_contact_per_30_days=1)
    prospect = make_prospect(product=product_educore)
    first = _make_draft(session, product_educore, prospect, subject="Premier")
    _approve(session, first)
    send_draft(
        session, draft=first, email_provider=email, actor="moi@exemple.example.org", settings=capped
    )

    second = _make_draft(session, product_educore, prospect, subject="Deuxième")
    _approve(session, second)
    send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=capped,
    )
    assert second.status != DraftStatus.BLOCKED

    later = datetime.now(UTC) + timedelta(days=31)
    renewed = request_approval(
        session,
        subject_type=ApprovalSubject.MESSAGE,
        subject_id=second.id,
        requested_by="auteur@exemple.example.org",
        summary="Message",
        draft=second,
        now=later,
    )
    decide_approval(
        session,
        approval_id=renewed.id,
        decided_by="valideur@exemple.example.org",
        approve=True,
        now=later,
    )
    outcome = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=capped,
        now=later,
    )
    assert outcome.sent is True


def test_campaign_daily_cap_is_enforced(
    session, product_educore, campaign_educore, make_prospect, email
):
    capped = Settings(email_max_per_campaign_per_day=1, email_max_per_contact_per_30_days=10)
    first_prospect = make_prospect(
        product=product_educore, domain="un.example.org", campaign=campaign_educore
    )
    second_prospect = make_prospect(
        product=product_educore, domain="deux.example.org", campaign=campaign_educore
    )

    first = _make_draft(session, product_educore, first_prospect)
    _approve(session, first)
    send_draft(
        session, draft=first, email_provider=email, actor="moi@exemple.example.org", settings=capped
    )

    second = _make_draft(session, product_educore, second_prospect)
    _approve(session, second)
    outcome = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=capped,
    )
    assert outcome.reason_code == "frequency_campaign"


def test_draft_without_recipient_is_refused(
    session, product_educore, make_prospect, email, settings
):
    prospect = make_prospect(product=product_educore, contact_email=None, domain="sans.example.org")
    draft = _make_draft(session, product_educore, prospect)
    _approve(session, draft)
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.reason_code == "no_recipient"


def test_every_block_leaves_an_auditable_event(
    session, product_educore, make_prospect, email, settings
):
    from growth_agent.models.ops import AuditEvent

    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    audits = session.query(AuditEvent).filter(AuditEvent.action == "outreach.send_blocked").all()
    assert len(audits) == 1
    assert audits[0].outcome == "blocked"


@pytest.mark.parametrize("enabled", [False, True])
def test_real_sending_requires_both_flag_and_provider(enabled):
    """Le double verrou : le drapeau seul ne suffit pas à activer l'envoi."""
    settings = Settings(
        email_sending_enabled=enabled,
        email_provider="mock",
        email_from="expediteur@exemple.example.org",
    )
    assert settings.can_send_email() is False


def test_expired_approval_blocks_the_send(session, product_educore, make_prospect, email, settings):
    """Une validation vieille de plus de 72 heures ne vaut plus : il faut
    refaire valider, pas réutiliser un accord périmé."""
    draft = _make_draft(session, product_educore, make_prospect(product=product_educore))
    _approve(session, draft)
    much_later = datetime.now(UTC) + timedelta(days=10)
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
        now=much_later,
    )
    assert outcome.sent is False
    assert "expiré" in outcome.detail
