"""Pannes et reprise : ce qui se passe quand l'envoi tourne mal.

Le protocole est « réserver, envoyer, confirmer ». Ces tests coupent le
courant à chacun des trois temps et vérifient la seule propriété qui compte :
**un message ne part jamais deux fois**, et un message dont le sort est
inconnu n'est jamais rejoué tout seul.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from doubles import Crash, ScriptedProvider, sending_settings
from growth_agent.config import Settings
from growth_agent.models.enums import (
    ApprovalSubject,
    DeliveryState,
    DraftStatus,
    OutreachEventType,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.services.analytics import compute_product_kpis
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.outreach import send_draft


@pytest.fixture
def real_settings() -> Settings:
    return sending_settings()


def _approved_draft(session, product, prospect, *, subject="Objet") -> MessageDraft:
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject=subject,
        body="Bonjour. Répondez STOP pour ne plus recevoir nos messages.",
    )
    session.add(draft)
    session.flush()
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
    session.commit()
    return draft


def _sent_events(session, draft) -> list[OutreachEvent]:
    return list(
        session.execute(
            select(OutreachEvent).where(
                OutreachEvent.draft_id == draft.id,
                OutreachEvent.event_type == OutreachEventType.SENT,
            )
        ).scalars()
    )


# --- 1. Panne AVANT la remise au fournisseur -------------------------------


def test_crash_before_handoff_leaves_a_reservation_and_blocks_the_retry(
    session_factory, session, product_educore, make_prospect, real_settings
):
    prospect = make_prospect(product=product_educore, contact_email="a@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)
    provider = ScriptedProvider("crash_before_handoff")

    with pytest.raises(Crash):
        send_draft(
            session,
            draft=draft,
            email_provider=provider,
            actor="moi@exemple.example.org",
            settings=real_settings,
        )
    assert provider.outbox == []

    # Redémarrage : nouvelle session, le brouillon est relu depuis la base.
    fresh = session_factory()
    try:
        reloaded = fresh.get(MessageDraft, draft.id)
        events = _sent_events(fresh, reloaded)
        assert len(events) == 1
        assert events[0].delivery_state == DeliveryState.RESERVED

        retry = send_draft(
            fresh,
            draft=reloaded,
            email_provider=ScriptedProvider("accept"),
            actor="moi@exemple.example.org",
            settings=real_settings,
        )
        assert retry.sent is False
        assert retry.reason_code == "duplicate"
        assert len(_sent_events(fresh, reloaded)) == 1
    finally:
        fresh.close()


# --- 2. Panne APRÈS la réservation, pendant l'attente de la réponse --------


def test_provider_timeout_marks_the_send_uncertain_and_never_replays_it(
    session_factory, session, product_educore, make_prospect, real_settings
):
    prospect = make_prospect(product=product_educore, contact_email="b@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)

    outcome = send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("timeout"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    assert outcome.sent is False
    assert outcome.reason_code == "uncertain"
    assert outcome.delivery_state == DeliveryState.UNCERTAIN

    events = _sent_events(session, draft)
    assert len(events) == 1
    assert events[0].delivery_state == DeliveryState.UNCERTAIN
    # La réservation n'est pas effacée : la trace d'une issue inconnue doit
    # survivre, c'est elle qui empêche le rejeu.
    assert events[0].payload["error_type"] == "TimeoutError"

    fresh = session_factory()
    try:
        reloaded = fresh.get(MessageDraft, draft.id)
        provider = ScriptedProvider("accept")
        retry = send_draft(
            fresh,
            draft=reloaded,
            email_provider=provider,
            actor="moi@exemple.example.org",
            settings=real_settings,
        )
        assert retry.sent is False
        assert retry.reason_code == "uncertain"
        assert provider.calls == []  # le fournisseur n'a même pas été appelé
        assert len(_sent_events(fresh, reloaded)) == 1
    finally:
        fresh.close()


# --- 3. Panne APRÈS acceptation possible par le fournisseur ----------------


def test_crash_after_handoff_does_not_send_twice_after_restart(
    session_factory, session, product_educore, make_prospect, real_settings
):
    prospect = make_prospect(product=product_educore, contact_email="c@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)
    provider = ScriptedProvider("crash_after_handoff")

    with pytest.raises(Crash):
        send_draft(
            session,
            draft=draft,
            email_provider=provider,
            actor="moi@exemple.example.org",
            settings=real_settings,
        )
    # Le message est bel et bien parti, mais la confirmation n'a pas eu lieu.
    assert len(provider.outbox) == 1

    fresh = session_factory()
    try:
        reloaded = fresh.get(MessageDraft, draft.id)
        assert reloaded.status not in {DraftStatus.SENT, DraftStatus.SENT_SIMULATED}

        second = ScriptedProvider("accept")
        retry = send_draft(
            fresh,
            draft=reloaded,
            email_provider=second,
            actor="moi@exemple.example.org",
            settings=real_settings,
        )
        assert retry.sent is False
        assert retry.reason_code == "duplicate"
        assert second.outbox == []  # le destinataire ne reçoit pas le message deux fois
    finally:
        fresh.close()


# --- 4. Refus explicite : rien n'est parti, la reprise est permise ---------


def test_explicit_refusal_releases_the_reservation(
    session, product_educore, make_prospect, real_settings
):
    prospect = make_prospect(product=product_educore, contact_email="d@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)

    refused = send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("refuse"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    assert refused.sent is False
    assert refused.reason_code == "provider_refused"
    # Un refus est explicite : rien n'a été remis, la réservation est levée.
    assert _sent_events(session, draft) == []
    assert draft.status != DraftStatus.BLOCKED

    retried = send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("accept"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    assert retried.sent is True
    assert len(_sent_events(session, draft)) == 1


# --- 5. Simulé et réel ne se mélangent jamais ------------------------------


def test_mock_provider_marks_the_event_simulated(
    session, product_educore, make_prospect, email, settings
):
    prospect = make_prospect(product=product_educore, contact_email="e@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)

    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is True
    assert outcome.simulated is True
    assert draft.status == DraftStatus.SENT_SIMULATED

    event = _sent_events(session, draft)[0]
    assert event.is_simulated is True
    assert event.delivery_state == DeliveryState.SENT


def test_real_provider_marks_the_event_real(session, product_educore, make_prospect, real_settings):
    prospect = make_prospect(product=product_educore, contact_email="f@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)

    outcome = send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("accept"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    assert outcome.sent is True
    assert outcome.simulated is False
    assert draft.status == DraftStatus.SENT

    event = _sent_events(session, draft)[0]
    assert event.is_simulated is False
    assert event.provider_message_id.startswith("smtp-")


def test_a_rehearsal_does_not_consume_the_real_idempotency_key(
    session, product_educore, make_prospect, email, real_settings, settings
):
    """Essayer en simulation puis envoyer pour de vrai doit rester possible."""
    prospect = make_prospect(product=product_educore, contact_email="g@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)

    rehearsal = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert rehearsal.simulated is True

    draft.status = DraftStatus.APPROVED  # le brouillon est repris pour l'envoi réel
    real = send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("accept"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    assert real.sent is True
    assert real.simulated is False

    events = _sent_events(session, draft)
    assert {e.is_simulated for e in events} == {True, False}


def test_simulated_sends_are_never_counted_as_contacted(
    session, product_educore, make_prospect, email, settings
):
    for index in range(3):
        prospect = make_prospect(
            product=product_educore,
            contact_email=f"sim{index}@exemple.example.org",
            domain=f"sim{index}.example.org",
        )
        draft = _approved_draft(session, product_educore, prospect, subject=f"Objet {index}")
        assert (
            send_draft(
                session,
                draft=draft,
                email_provider=email,
                actor="moi@exemple.example.org",
                settings=settings,
            ).sent
            is True
        )
    session.commit()

    kpis = compute_product_kpis(session, product_id=product_educore.id)
    assert kpis.funnel.contacted == 0
    assert kpis.simulated_total == 3
    assert any("simul" in note.lower() for note in kpis.notes)

    visible = compute_product_kpis(session, product_id=product_educore.id, include_simulated=True)
    assert visible.funnel.contacted == 3


def test_reserved_and_uncertain_sends_are_not_counted_either(
    session, product_educore, make_prospect, real_settings
):
    prospect = make_prospect(product=product_educore, contact_email="h@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)
    send_draft(
        session,
        draft=draft,
        email_provider=ScriptedProvider("timeout"),
        actor="moi@exemple.example.org",
        settings=real_settings,
    )
    session.commit()

    kpis = compute_product_kpis(session, product_id=product_educore.id)
    assert kpis.funnel.contacted == 0
    assert kpis.uncertain_total == 1
    assert any("inconnue" in note.lower() or "incertain" in note.lower() for note in kpis.notes)
