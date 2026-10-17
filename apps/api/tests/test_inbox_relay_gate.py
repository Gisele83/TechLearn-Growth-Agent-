"""Le contrôle de fraîcheur du relevé, avant toute relance.

Le risque : une opposition dort dans une boîte que personne n'a relevée, et
une relance part pendant ce temps. Le garde-fou suspend la relance plutôt
que de l'envoyer.

Deux limites, posées explicitement :

* il ne s'applique qu'aux **relances** — un premier contact n'a pas pu
  susciter de réponse, et le bloquer n'empêcherait rien ;
* il ne passe **jamais** avant le contrôle d'opposition. Une opposition déjà
  enregistrée reste traitée immédiatement, relevé en retard ou non. Ce
  garde-fou protège de ce qu'on ignore, jamais au prix de ce qu'on sait.

Aucun envoi réel : le fournisseur est une doublure en mémoire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from doubles import ScriptedProvider, sending_settings
from growth_agent.config import Settings
from growth_agent.models.enums import (
    ApprovalSubject,
    DraftStatus,
    OutreachEventType,
    SuppressionScope,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.audit import record_audit
from growth_agent.services.inbox_relay import RELAY_DECLARED_ACTION, check_freshness
from growth_agent.services.outreach import send_draft
from growth_agent.services.suppression import add_suppression

#: Configuration complète : la boîte est promise, donc un relevé est dû.
MONITORED = sending_settings(
    email_from="noreply@techlearn-saem.com",
    reply_to_email="contact@techlearn-saem.com",
    inbox_relay_owner_email="merveille@techlearn-saem.com",
    inbox_relay_deputy_email="suppleance@techlearn-saem.com",
)


def _declare_relay(session, *, ago=timedelta(0)) -> None:
    record_audit(
        session,
        actor="merveille@techlearn-saem.com",
        actor_role="commercial",
        action=RELAY_DECLARED_ACTION,
        object_type="reply_inbox",
        now=datetime.now(UTC) - ago,
    )
    session.flush()


def _approved(session, product, prospect, *, subject) -> MessageDraft:
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject=subject,
        body=(
            "Bonjour.\n\nEduCore regroupe inscriptions et absences.\n\n"
            "Si ce n'est pas votre sujet, répondez STOP et je ne vous "
            "recontacte pas.\n\nAwa Njoya"
        ),
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
    return draft


def _send(session, draft, provider, settings=MONITORED):
    return send_draft(
        session,
        draft=draft,
        email_provider=provider,
        actor="moi@exemple.example.org",
        settings=settings,
    )


# --- Le contrôle lui-même ---------------------------------------------------


def test_no_declaration_at_all_is_not_fresh(session):
    etat = check_freshness(session, settings=MONITORED)
    assert etat.applicable is True
    assert etat.fresh is False
    assert etat.declared_at is None


def test_a_recent_declaration_is_fresh(session):
    _declare_relay(session)
    assert check_freshness(session, settings=MONITORED).fresh is True


def test_a_weekend_old_declaration_is_still_fresh(session):
    """Vendredi matin puis lundi matin : 72 heures parfaitement normales."""
    _declare_relay(session, ago=timedelta(hours=72))
    assert check_freshness(session, settings=MONITORED).fresh is True


def test_a_five_day_old_declaration_is_not(session):
    _declare_relay(session, ago=timedelta(days=5))
    assert check_freshness(session, settings=MONITORED).fresh is False


def test_the_threshold_is_configurable(session):
    _declare_relay(session, ago=timedelta(hours=30))
    strict = sending_settings(
        email_from="noreply@techlearn-saem.com",
        reply_to_email="contact@techlearn-saem.com",
        inbox_relay_max_age_hours=24,
    )
    assert check_freshness(session, settings=strict).fresh is False


def test_without_a_promised_mailbox_there_is_nothing_to_check(session):
    """Les messages n'invitent pas à répondre : aucun relevé n'est dû, et
    bloquer des envois pour une obligation inexistante serait absurde."""
    etat = check_freshness(session, settings=Settings())
    assert etat.applicable is False
    assert etat.fresh is True


# --- Effet sur l'envoi ------------------------------------------------------


def test_a_first_contact_goes_out_even_without_any_declaration(
    session, product_educore, make_prospect
):
    """Personne n'a encore pu répondre : rien ne justifie de retenir."""
    prospect = make_prospect(product=product_educore, contact_email="premier@exemple.example.org")
    draft = _approved(session, product_educore, prospect, subject="Premier contact")
    provider = ScriptedProvider()
    assert _send(session, draft, provider).sent is True
    assert len(provider.outbox) == 1


def test_a_follow_up_is_held_when_the_declaration_is_stale(session, product_educore, make_prospect):
    prospect = make_prospect(product=product_educore, contact_email="suite@exemple.example.org")
    provider = ScriptedProvider()
    _send(session, _approved(session, product_educore, prospect, subject="Premier"), provider)
    _declare_relay(session, ago=timedelta(days=6))

    relance = _approved(session, product_educore, prospect, subject="Relance")
    issue = _send(session, relance, provider)

    assert issue.sent is False
    assert issue.reason_code == "stale_inbox"
    assert "opposition pourrait dormir" in issue.detail
    assert len(provider.outbox) == 1, "la relance est partie malgré le garde-fou"


def test_the_held_draft_is_not_condemned(session, product_educore, make_prospect):
    """Motif transitoire : le brouillon repart dès qu'un relevé est déclaré."""
    prospect = make_prospect(product=product_educore, contact_email="repart@exemple.example.org")
    provider = ScriptedProvider()
    _send(session, _approved(session, product_educore, prospect, subject="Premier"), provider)
    _declare_relay(session, ago=timedelta(days=6))

    relance = _approved(session, product_educore, prospect, subject="Relance")
    assert _send(session, relance, provider).sent is False
    assert relance.status != DraftStatus.BLOCKED

    _declare_relay(session)
    assert _send(session, relance, provider).sent is True
    assert len(provider.outbox) == 2


def test_the_hold_is_recorded_as_a_blocked_event(session, product_educore, make_prospect):
    """Une suspension silencieuse serait indiscernable d'un oubli."""
    prospect = make_prospect(product=product_educore, contact_email="trace@exemple.example.org")
    provider = ScriptedProvider()
    _send(session, _approved(session, product_educore, prospect, subject="Premier"), provider)
    _declare_relay(session, ago=timedelta(days=6))
    relance = _approved(session, product_educore, prospect, subject="Relance")
    _send(session, relance, provider)
    session.flush()

    motifs = [
        event.payload.get("reason_code")
        for event in session.query(OutreachEvent)
        .filter(OutreachEvent.event_type == OutreachEventType.SEND_BLOCKED)
        .all()
    ]
    assert "stale_inbox" in motifs


def test_follow_ups_are_not_held_when_no_mailbox_is_promised(
    session, product_educore, make_prospect
):
    sans_boite = sending_settings(email_from="noreply@techlearn-saem.com")
    assert sans_boite.replies_are_monitored() is False

    prospect = make_prospect(product=product_educore, contact_email="sans@exemple.example.org")
    provider = ScriptedProvider()
    _send(
        session,
        _approved(session, product_educore, prospect, subject="Premier"),
        provider,
        settings=sans_boite,
    )
    relance = _approved(session, product_educore, prospect, subject="Relance")
    assert _send(session, relance, provider, settings=sans_boite).sent is True


# --- L'opposition passe avant tout -----------------------------------------


def test_a_recorded_objection_is_honoured_even_with_a_stale_inbox(
    session, product_educore, make_prospect
):
    """Le point le plus important du garde-fou : il ne doit jamais retarder
    le respect d'une opposition déjà connue. Un relevé en retard ne change
    rien à ce qu'on sait déjà."""
    prospect = make_prospect(product=product_educore, contact_email="oppose@exemple.example.org")
    provider = ScriptedProvider()
    _send(session, _approved(session, product_educore, prospect, subject="Premier"), provider)

    add_suppression(session, value="oppose@exemple.example.org", scope=SuppressionScope.GLOBAL)
    _declare_relay(session, ago=timedelta(days=10))
    session.flush()

    relance = _approved(session, product_educore, prospect, subject="Relance")
    issue = _send(session, relance, provider)

    # Le motif est l'opposition, pas le relevé : le contact est écarté pour
    # ce qu'on sait, pas mis en attente pour ce qu'on ignore.
    assert issue.sent is False
    assert issue.reason_code == "suppressed"
    assert relance.status == DraftStatus.BLOCKED
    assert len(provider.outbox) == 1


@pytest.mark.parametrize("anciennete", [timedelta(0), timedelta(days=10)])
def test_an_objection_can_always_be_recorded_whatever_the_relay_age(
    session, product_educore, make_prospect, llm, ctx, anciennete
):
    """Le garde-fou porte sur l'envoi, jamais sur l'enregistrement d'une
    opposition : celle-ci doit pouvoir entrer à tout moment."""
    from growth_agent.agents.conversation_analyst import ConversationAnalyst
    from growth_agent.models.outreach import SuppressionEntry

    prospect = make_prospect(product=product_educore, contact_email="entrant@exemple.example.org")
    _declare_relay(session, ago=anciennete)
    ConversationAnalyst(llm).execute(ctx, prospect=prospect, reply_text="STOP")
    session.flush()

    assert (
        session.query(SuppressionEntry)
        .filter(SuppressionEntry.normalized_value == "entrant@exemple.example.org")
        .count()
        == 1
    )
