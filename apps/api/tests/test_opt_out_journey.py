"""Le parcours d'opposition, de bout en bout.

Un message part, le prospect répond « STOP », et la question est simple :
**est-il réellement protégé d'un nouvel envoi, tout de suite ?**

Ce que ces tests couvrent : la détection, l'enregistrement de l'opposition,
la mise à jour du prospect, et le blocage immédiat.

Ce qu'ils ne couvrent **pas**, et qui reste à faire : la *réception* du
message de réponse. Aucune intégration de boîte entrante n'existe. La réponse
est ici injectée dans l'analyste comme si elle venait d'arriver. Tant que la
réception n'est pas branchée et éprouvée, l'analyse des réponses n'est pas
opérationnelle — et rien dans le produit ne doit prétendre le contraire.
"""

from __future__ import annotations

import pytest

from growth_agent.agents.conversation_analyst import ConversationAnalyst
from growth_agent.config import Settings
from growth_agent.models.enums import (
    ApprovalSubject,
    DraftStatus,
    ProspectStage,
    SuppressionScope,
)
from growth_agent.models.outreach import MessageDraft, SuppressionEntry
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.opt_out_language import closing_line, is_opt_out
from growth_agent.services.outreach import send_draft, with_unsubscribe_footer

MONITORED = Settings(
    email_from="noreply@techlearn-saem.com",
    reply_to_email="contact@techlearn-saem.com",
)
UNMONITORED = Settings(email_from="noreply@techlearn-saem.com")


DEFAULT_BODY = (
    "Bonjour.\n\nEduCore regroupe inscriptions et absences.\n\n"
    "Si ce n'est pas votre sujet, le lien de désinscription en bas de ce "
    "message vous retire de nos envois.\n\nAwa Njoya"
)


def _approved_draft(session, product, prospect, *, subject="Objet", body=None) -> MessageDraft:
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject=subject,
        body=body or DEFAULT_BODY,
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


# --- La promesse faite dans le message correspond à la réalité ------------


def test_without_a_monitored_mailbox_the_message_does_not_invite_a_reply():
    """Promettre « répondez STOP » depuis une adresse qui ne reçoit pas est
    pire que ne rien promettre : la personne croit s'être opposée."""
    assert UNMONITORED.replies_are_monitored() is False
    ligne = closing_line(replies_are_monitored=False)
    assert "STOP" not in ligne
    assert "désinscription" in ligne


def test_with_a_monitored_mailbox_the_message_may_invite_a_reply():
    assert MONITORED.replies_are_monitored() is True
    assert "STOP" in closing_line(replies_are_monitored=True)


def test_the_confirmed_mailbox_turns_the_invitation_back_on():
    """contact@techlearn-saem.com, confirmée le 16/10/2026. L'expéditeur
    reste noreply@ : c'est le Reply-To qui rend la promesse tenable."""
    configuree = Settings(
        email_from="noreply@techlearn-saem.com",
        reply_to_email="contact@techlearn-saem.com",
    )
    assert configuree.replies_are_monitored() is True


def test_a_monitored_message_still_carries_the_link(session, product_educore, make_prospect, email):
    """Les deux moyens coexistent. Le lien ne dépend de personne ; la
    réponse, si. Retirer le lien parce qu'on invite à répondre reviendrait à
    faire dépendre un droit d'une tâche humaine quotidienne."""
    prospect = make_prospect(product=product_educore, contact_email="deux@exemple.example.org")
    draft = _approved_draft(
        session,
        product_educore,
        prospect,
        body=(
            "Bonjour.\n\nEduCore regroupe inscriptions et absences.\n\n"
            "Si ce n'est pas votre sujet, répondez STOP et je ne vous "
            "recontacte pas.\n\nAwa Njoya"
        ),
    )
    send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=MONITORED,
    )
    envoye = email.outbox[0]
    assert "STOP" in envoye.body
    assert envoye.headers["List-Unsubscribe"].strip("<>") in envoye.body


def test_the_configured_reply_address_reaches_the_provider():
    """Sans Reply-To sur le message remis, la réponse partirait vers
    noreply@ et se perdrait — la promesse redeviendrait creuse."""
    from growth_agent.connectors.email.factory import get_email_provider, reset_email_singleton

    configuree = Settings(
        email_provider="sendgrid",
        email_sending_enabled=True,
        sendgrid_api_key="SG.jeton-de-test",
        email_from="noreply@techlearn-saem.com",
        reply_to_email="contact@techlearn-saem.com",
        public_base_url="https://go.techlearn-saem.com",
    )
    reset_email_singleton()
    try:
        provider = get_email_provider(configuree)
        assert provider.provider == "sendgrid"
        assert provider._reply_to == "contact@techlearn-saem.com"
    finally:
        reset_email_singleton()


def test_the_sent_message_always_carries_a_visible_unsubscribe_link(
    session, product_educore, make_prospect, email, settings
):
    """L'en-tête RFC 8058 alimente le bouton natif de Gmail, mais reste
    invisible ailleurs. Le lien doit aussi être lisible dans le texte."""
    prospect = make_prospect(product=product_educore, contact_email="cible@exemple.example.org")
    draft = _approved_draft(session, product_educore, prospect)
    assert send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    ).sent

    envoye = email.outbox[0]
    header_url = envoye.headers["List-Unsubscribe"].strip("<>")
    assert header_url in envoye.body, "le lien n'est visible que dans l'en-tête"
    assert "Pour ne plus recevoir" in envoye.body


def test_the_footer_is_added_without_touching_the_stored_draft(
    session, product_educore, make_prospect, email, settings
):
    """Le pied est une mention légale ajoutée à l'envoi. Le texte approuvé,
    lui, reste celui que l'auteur a écrit et que le valideur a lu."""
    prospect = make_prospect(product=product_educore)
    draft = _approved_draft(session, product_educore, prospect)
    texte_approuve = draft.body
    send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert draft.body == texte_approuve
    assert "Pour ne plus recevoir" not in draft.body


def test_no_footer_without_a_public_address():
    assert with_unsubscribe_footer("Bonjour.", "") == "Bonjour."


# --- Détection : les formulations réelles, pas seulement « STOP » ---------


@pytest.mark.parametrize(
    "reponse",
    [
        "STOP",
        "stop",
        "Stop merci",
        "Désinscription s'il vous plaît",
        "Desinscription",
        "Merci de ne plus me contacter.",
        "Je ne souhaite plus recevoir vos messages",
        "Retirez-moi de votre liste",
        "Supprimez mon adresse de vos fichiers",
        "Je m'oppose à l'utilisation de mes données",
        "Arrêtez de m'écrire",
        "Please remove me from your list",
        "unsubscribe",
        "Laissez-moi tranquille",
        "Cessez de m'envoyer des mails",
    ],
)
def test_every_realistic_phrasing_is_recognised(reponse):
    assert is_opt_out(reponse) is True, f"non reconnu : {reponse!r}"


@pytest.mark.parametrize(
    "reponse",
    [
        "Bonjour, oui c'est un sujet d'actualité.",
        "Pouvez-vous me rappeler la semaine prochaine ?",
        "Je transfère à ma collègue.",
        "Nous avons déjà un outil, merci.",
    ],
)
def test_an_ordinary_reply_is_not_mistaken_for_an_objection(reponse):
    assert is_opt_out(reponse) is False, f"faux positif : {reponse!r}"


# --- Le parcours complet ---------------------------------------------------


def test_a_stop_reply_blocks_the_next_send_immediately(
    session, product_educore, make_prospect, email, llm, settings, ctx
):
    """Le test qui compte : envoi, réponse STOP, et plus rien ne part."""
    prospect = make_prospect(product=product_educore, contact_email="refus@exemple.example.org")
    premier = _approved_draft(session, product_educore, prospect, subject="Premier")
    assert (
        send_draft(
            session,
            draft=premier,
            email_provider=email,
            actor="moi@exemple.example.org",
            settings=settings,
        ).sent
        is True
    )
    assert len(email.outbox) == 1

    # La réponse arrive. Elle est ici injectée : aucune réception réelle
    # n'existe encore, et c'est précisément la limite à ne pas masquer.
    resultat = ConversationAnalyst(llm).execute(
        ctx,
        prospect=prospect,
        reply_text="Merci de ne plus me contacter.",
    )
    session.flush()

    assert resultat.value.opted_out is True
    assert prospect.stage == ProspectStage.LOST

    entree = (
        session.query(SuppressionEntry)
        .filter(SuppressionEntry.normalized_value == "refus@exemple.example.org")
        .one()
    )
    assert entree.scope == SuppressionScope.GLOBAL

    # Et l'envoi suivant est refusé, sans que rien ne quitte la machine.
    second = _approved_draft(session, product_educore, prospect, subject="Deuxième")
    refus = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert refus.sent is False
    assert refus.reason_code == "suppressed"
    assert second.status == DraftStatus.BLOCKED
    assert len(email.outbox) == 1, "un message est parti après l'opposition"


def test_the_objection_survives_a_restart(
    session_factory, session, product_educore, make_prospect, llm, ctx
):
    """Une opposition tenue en mémoire ne protège personne."""
    prospect = make_prospect(product=product_educore, contact_email="durable@exemple.example.org")
    ConversationAnalyst(llm).execute(ctx, prospect=prospect, reply_text="STOP")
    session.commit()

    fraiche = session_factory()
    try:
        assert (
            fraiche.query(SuppressionEntry)
            .filter(SuppressionEntry.normalized_value == "durable@exemple.example.org")
            .count()
            == 1
        )
    finally:
        fraiche.close()
