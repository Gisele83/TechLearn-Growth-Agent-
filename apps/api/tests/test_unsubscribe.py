"""Désinscription : le lien doit marcher pour de vrai.

Un lien de désinscription qui échoue est pire que pas de lien du tout — il
promet un droit qu'il ne tient pas. Ces tests vérifient donc le trajet
complet : le jeton inséré dans l'en-tête du message, le clic sans compte, et
l'effet réel sur l'envoi suivant.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from growth_agent.models.enums import ApprovalSubject, DraftStatus, SuppressionScope
from growth_agent.models.outreach import MessageDraft, SuppressionEntry, UnsubscribeToken
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.outreach import send_draft
from growth_agent.services.unsubscribe import (
    TOKEN_BYTES,
    find_token,
    hash_token,
    issue_token,
    process_unsubscribe,
    revoke_token,
)


def _make_draft(session, product, prospect) -> MessageDraft:
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject="Objet",
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
    return draft


# --- Imprévisibilité du jeton ---------------------------------------------


def test_token_carries_256_bits_of_entropy(session, product_educore):
    assert TOKEN_BYTES == 32
    issued = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    # 32 octets en base64url sans remplissage : 43 caractères.
    assert len(issued.raw_token) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", issued.raw_token)


def test_tokens_are_never_repeated_and_look_random(session, product_educore):
    tokens = [
        issue_token(session, email="a@exemple.example.org", product_id=product_educore.id).raw_token
        for _ in range(200)
    ]
    assert len(set(tokens)) == 200

    # Un générateur prévisible (compteur, horodatage, hachage d'adresse)
    # laisserait un préfixe commun ou une distribution dégénérée.
    assert len({t[:8] for t in tokens}) == 200
    alphabet = Counter("".join(tokens))
    # Aucun caractère ne doit dominer : avec 64 symboles possibles, plus de
    # 10 % d'occurrences pour un seul signalerait une structure.
    assert max(alphabet.values()) / sum(alphabet.values()) < 0.10


def test_database_never_stores_the_token_in_clear(session, product_educore):
    issued = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    session.commit()
    stored = session.get(UnsubscribeToken, issued.record.id)
    assert stored is not None
    assert stored.token_hash == hash_token(issued.raw_token)
    assert issued.raw_token not in stored.token_hash
    # Un jeton ne peut pas être reconstitué depuis la base : seule la
    # reconnaissance est possible.
    assert find_token(session, issued.raw_token) is not None
    assert find_token(session, issued.raw_token[:-1] + "x") is None


def test_tokens_issued_for_the_same_address_differ(session, product_educore):
    first = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    second = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    assert first.raw_token != second.raw_token


# --- Traitement du clic ----------------------------------------------------


def test_click_suppresses_the_address(session, product_educore):
    issued = issue_token(session, email="Qui@Exemple.Example.ORG", product_id=product_educore.id)
    result = process_unsubscribe(session, raw_token=issued.raw_token)
    assert result.accepted is True
    assert result.already_done is False
    entry = (
        session.query(SuppressionEntry)
        .filter(SuppressionEntry.normalized_value == "qui@exemple.example.org")
        .one()
    )
    assert entry.scope == SuppressionScope.GLOBAL


def test_second_click_confirms_without_error(session, product_educore):
    issued = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    process_unsubscribe(session, raw_token=issued.raw_token)
    again = process_unsubscribe(session, raw_token=issued.raw_token)
    assert again.accepted is True
    assert again.already_done is True
    assert session.query(SuppressionEntry).count() == 1


def test_unknown_token_reveals_nothing(session):
    result = process_unsubscribe(session, raw_token="x" * 43)
    assert result.accepted is False
    assert result.reason == "unknown_token"
    assert session.query(SuppressionEntry).count() == 0


def test_revoked_token_stops_working(session, product_educore):
    issued = issue_token(session, email="a@exemple.example.org", product_id=product_educore.id)
    assert revoke_token(session, issued.record.id) is True
    result = process_unsubscribe(session, raw_token=issued.raw_token)
    assert result.accepted is False
    assert result.reason == "revoked_token"
    assert session.query(SuppressionEntry).count() == 0


# --- Trajet complet : message envoyé → lien cliqué → envoi suivant refusé --


def test_link_from_the_sent_message_actually_unsubscribes(
    session, product_educore, make_prospect, email, settings
):
    prospect = make_prospect(product=product_educore, contact_email="cible@exemple.example.org")
    draft = _make_draft(session, product_educore, prospect)
    outcome = send_draft(
        session,
        draft=draft,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert outcome.sent is True
    assert len(email.outbox) == 1

    # Le lien tel qu'il part réellement, extrait de l'en-tête RFC 8058.
    header = email.outbox[0].headers["List-Unsubscribe"]
    url = header.strip("<>")
    raw_token = url.rsplit("/", 1)[-1]

    result = process_unsubscribe(session, raw_token=raw_token)
    assert result.accepted is True

    # Et l'envoi suivant, vers la même adresse, est refusé.
    second = _make_draft(session, product_educore, prospect)
    second.subject = "Autre objet"
    blocked = send_draft(
        session,
        draft=second,
        email_provider=email,
        actor="moi@exemple.example.org",
        settings=settings,
    )
    assert blocked.sent is False
    assert blocked.reason_code == "suppressed"
    assert second.status == DraftStatus.BLOCKED
    assert len(email.outbox) == 1


# --- Routes publiques : pas de compte, pas de jeton d'API ------------------


def _issued_for(client, session_factory, product_id, address="public@exemple.example.org"):
    s = session_factory()
    try:
        issued = issue_token(s, email=address, product_id=product_id)
        s.commit()
        return issued.raw_token
    finally:
        s.close()


def test_one_click_post_works_without_authentication(client, session_factory, product_educore):
    raw = _issued_for(client, session_factory, product_educore.id)
    response = client.post(f"/unsubscribe/{raw}")
    assert response.status_code == 200
    assert response.json() == {"status": "unsubscribed"}
    assert "authorization" not in {k.lower() for k in response.request.headers}


def test_one_click_post_is_idempotent(client, session_factory, product_educore):
    raw = _issued_for(client, session_factory, product_educore.id)
    assert client.post(f"/unsubscribe/{raw}").status_code == 200
    assert client.post(f"/unsubscribe/{raw}").status_code == 200

    s = session_factory()
    try:
        assert s.query(SuppressionEntry).count() == 1
    finally:
        s.close()


def test_unknown_token_answers_404_without_detail(client):
    response = client.post("/unsubscribe/" + "z" * 43)
    assert response.status_code == 404
    assert response.json() == {"status": "unknown"}


def test_human_page_is_self_contained(client, session_factory, product_educore):
    raw = _issued_for(client, session_factory, product_educore.id)
    response = client.get(f"/unsubscribe/{raw}")
    assert response.status_code == 200
    body = response.text
    assert "C'est fait" in body
    # Ni script, ni ressource tierce, ni cookie : une page de désinscription
    # ne doit renseigner personne sur la personne qui se désinscrit.
    assert "<script" not in body.lower()
    assert not re.search(r'(src|href)\s*=\s*"https?://', body)
    assert "set-cookie" not in {k.lower() for k in response.headers}
    assert "noindex" in body


def test_human_page_on_unknown_token_stays_helpful(client):
    response = client.get("/unsubscribe/" + "y" * 43)
    assert response.status_code == 404
    assert "STOP" in response.text


@pytest.mark.parametrize("method", ["post", "get"])
def test_public_routes_are_outside_the_api_prefix(client, session_factory, product_educore, method):
    raw = _issued_for(client, session_factory, product_educore.id)
    # Le lien part dans un email : il doit être court et stable, pas préfixé
    # par la version de l'API.
    assert client.request(method, f"/api/v1/unsubscribe/{raw}").status_code == 404
    assert client.request(method, f"/unsubscribe/{raw}").status_code == 200
