"""API : authentification, rôles, cloisonnement des produits, erreurs."""

from __future__ import annotations

import pytest


def test_health_is_public_and_leaks_nothing(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["connectors"]) == {
        "crm",
        "llm",
        "search",
        "email",
        "maintrix",
        "educore",
    }
    # Aucune URL, aucun nom d'hôte, aucun jeton dans la sonde publique.
    serialized = response.text.lower()
    for forbidden in ("token", "password", "secret", "postgresql://", "redis://"):
        assert forbidden not in serialized


def test_email_sending_is_disabled_by_default(client):
    assert client.get("/health").json()["connectors"]["email"] == "disabled"


def test_product_connectors_default_to_mock(client):
    connectors = client.get("/health").json()["connectors"]
    assert connectors["maintrix"] == "mock"
    assert connectors["educore"] == "mock"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/products",
        "/api/v1/campaigns",
        "/api/v1/prospects",
        "/api/v1/drafts",
        "/api/v1/approvals",
        "/api/v1/kpis",
        "/api/v1/audit",
    ],
)
def test_every_business_endpoint_requires_authentication(client, seeded, path):
    assert client.get(path).status_code == 401


def test_invalid_credentials_are_refused_without_revealing_the_account(client, seeded):
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "inexistant@growth.example.org", "password": "x"},
    )
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@growth.example.org", "password": "mauvais"},
    )
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_a_tampered_token_is_refused(client, seeded, auth):
    headers = auth("admin")
    headers["Authorization"] = headers["Authorization"][:-4] + "AAAA"
    assert client.get("/api/v1/products", headers=headers).status_code == 401


def test_reader_can_read(client, seeded, auth):
    assert client.get("/api/v1/products", headers=auth("lecteur")).status_code == 200
    assert client.get("/api/v1/kpis", headers=auth("lecteur")).status_code == 200


def test_reader_cannot_write(client, seeded, auth):
    headers = auth("lecteur")
    assert (
        client.post(
            "/api/v1/campaigns",
            headers=headers,
            json={"product_code": "educore", "slug": "test", "name": "Test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/prospects/discover", headers=headers, json={"product_code": "educore"}
        ).status_code
        == 403
    )


# --- Le palier « operateur » : produire sans valider ----------------------


def _pending_approval(session_factory, requested_by="operateur@growth.example.org") -> str:
    """Un brouillon et sa demande de validation, posés directement en base.

    Le parcours de rédaction a ses propres épreuves ; ici on teste qui a le
    droit de décider, pas comment le message a été écrit.
    """
    import uuid as _uuid

    from sqlalchemy import select

    from growth_agent.models.catalog import Product
    from growth_agent.models.enums import ApprovalSubject
    from growth_agent.models.outreach import MessageDraft
    from growth_agent.models.prospect import ProspectCandidate
    from growth_agent.services.approvals import request_approval

    session = session_factory()
    try:
        produit = session.execute(select(Product).where(Product.code == "educore")).scalar_one()
        prospect = ProspectCandidate(
            product_id=produit.id,
            organization_name="Lycée de test",
            dedupe_key=f"domain:{_uuid.uuid4().hex[:8]}.example.org",
            domain=f"{_uuid.uuid4().hex[:8]}.example.org",
            country_code="CM",
            sector="Enseignement secondaire",
            contact_full_name="Contact",
            contact_role="Directeur",
            contact_email=f"{_uuid.uuid4().hex[:6]}@exemple.example.org",
        )
        session.add(prospect)
        session.flush()
        draft = MessageDraft(
            product_id=produit.id,
            prospect_id=prospect.id,
            recipient_email=prospect.contact_email,
            subject="Objet",
            body="Bonjour. Le lien de désinscription vous retire de nos envois.",
        )
        session.add(draft)
        session.flush()
        approbation = request_approval(
            session,
            subject_type=ApprovalSubject.MESSAGE,
            subject_id=draft.id,
            requested_by=requested_by,
            summary="Message",
            draft=draft,
        )
        session.commit()
        return str(approbation.id)
    finally:
        session.close()


def test_the_role_ladder_separates_producing_from_approving():
    """La matrice complète, en un coup d'œil. C'est le test qu'on relit quand
    on se demande ce qu'un rôle peut faire."""
    from growth_agent.models.enums import Role
    from growth_agent.security.rbac import (
        CAN_ADMINISTER,
        CAN_APPROVE,
        CAN_OPERATE,
        CAN_READ,
        has_role,
    )

    attendu = {
        Role.LECTEUR: (True, False, False, False),
        Role.OPERATEUR: (True, True, False, False),
        Role.COMMERCIAL: (True, True, True, False),
        Role.ADMIN: (True, True, True, True),
    }
    for role, droits in attendu.items():
        obtenu = tuple(
            has_role(role, permission)
            for permission in (CAN_READ, CAN_OPERATE, CAN_APPROVE, CAN_ADMINISTER)
        )
        assert obtenu == droits, f"{role} : {obtenu} au lieu de {droits}"


def test_an_operator_can_report_a_reply_and_declare_a_relay(client, seeded, auth, session_factory):
    """Le travail de la personne qui relève la boîte : reporter une réponse
    et déclarer un relevé. Les deux exigent d'écrire dans le système."""
    from growth_agent.models.prospect import ProspectCandidate

    headers = auth("operateur")
    assert client.post("/api/v1/replies/relay-check", headers=headers).status_code == 201

    _pending_approval(session_factory)
    session = session_factory()
    try:
        identifiant = str(session.query(ProspectCandidate).first().id)
    finally:
        session.close()

    reponse = client.post(
        "/api/v1/replies/analyze",
        headers=headers,
        json={"prospect_id": identifiant, "reply_text": "Merci de ne plus me contacter."},
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["opted_out"] is True


def test_an_operator_cannot_approve_a_message(client, seeded, auth, session_factory):
    """Le point de toute la séparation : produire n'est pas valider. Un envoi
    engage l'entreprise, et cette décision demande un second regard."""
    approbation = _pending_approval(session_factory)
    decision = client.post(
        f"/api/v1/approvals/{approbation}/decision",
        headers=auth("operateur"),
        json={"approve": True},
    )
    assert decision.status_code == 403, "un opérateur a pu approuver un message"


def test_an_operator_can_still_request_an_approval(client, seeded, auth, session_factory):
    """Demander n'est pas décider : l'opératrice doit pouvoir soumettre."""
    from growth_agent.models.outreach import MessageDraft

    _pending_approval(session_factory)
    session = session_factory()
    try:
        brouillon = session.query(MessageDraft).first()
        identifiant = str(brouillon.id)
    finally:
        session.close()

    demande = client.post(
        "/api/v1/approvals",
        headers=auth("operateur"),
        json={"draft_id": identifiant, "summary": "Nouvelle demande"},
    )
    assert demande.status_code == 201, demande.text


def test_a_commercial_can_still_approve(client, seeded, auth, session_factory):
    """La séparation ne doit rien retirer à qui devait déjà pouvoir."""
    approbation = _pending_approval(session_factory)
    decision = client.post(
        f"/api/v1/approvals/{approbation}/decision",
        headers=auth("commercial"),
        json={"approve": True},
    )
    assert decision.status_code == 200, decision.text


def test_an_operator_cannot_administer(client, seeded, auth):
    assert (
        client.post(
            "/api/v1/icps",
            headers=auth("operateur"),
            json={"product_code": "educore", "name": "Profil non autorisé"},
        ).status_code
        == 403
    )


def test_a_reader_can_neither_report_a_reply_nor_declare_a_relay(client, seeded, auth):
    assert client.post("/api/v1/replies/relay-check", headers=auth("lecteur")).status_code == 403


def test_commercial_cannot_administer(client, seeded, auth):
    response = client.post(
        "/api/v1/icps",
        headers=auth("commercial"),
        json={"product_code": "educore", "name": "Profil non autorisé"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert "admin" in response.json()["hint"]


def test_admin_can_administer(client, seeded, auth):
    response = client.post(
        "/api/v1/icps",
        headers=auth("admin"),
        json={"product_code": "educore", "name": "Nouveau profil", "target_sectors": ["X"]},
    )
    assert response.status_code == 201


def test_only_admin_can_remove_a_suppression(client, seeded, auth):
    client.post(
        "/api/v1/suppressions",
        headers=auth("commercial"),
        json={"value": "retrait@exemple.example.org"},
    )
    entry = client.get("/api/v1/suppressions", headers=auth("lecteur")).json()[0]
    assert (
        client.delete(f"/api/v1/suppressions/{entry['id']}", headers=auth("commercial")).status_code
        == 403
    )
    assert (
        client.delete(f"/api/v1/suppressions/{entry['id']}", headers=auth("admin")).status_code
        == 204
    )


def test_removing_a_suppression_is_audited(client, seeded, auth):
    client.post(
        "/api/v1/suppressions",
        headers=auth("commercial"),
        json={"value": "trace@exemple.example.org"},
    )
    entry = client.get("/api/v1/suppressions", headers=auth("admin")).json()[0]
    client.delete(f"/api/v1/suppressions/{entry['id']}", headers=auth("admin"))
    actions = [
        event["action"]
        for event in client.get("/api/v1/audit?limit=200", headers=auth("admin")).json()
    ]
    assert "suppression.removed" in actions


def test_an_icp_cannot_cross_product_boundaries(client, seeded, auth):
    icp = client.post(
        "/api/v1/icps",
        headers=auth("admin"),
        json={"product_code": "maintrix", "name": "Profil Maintrix"},
    ).json()
    response = client.post(
        "/api/v1/campaigns",
        headers=auth("commercial"),
        json={
            "product_code": "educore",
            "slug": "melange",
            "name": "Campagne mélangée",
            "icp_id": icp["id"],
        },
    )
    assert response.status_code == 422
    assert "n'appartient pas à ce produit" in response.json()["detail"]


def test_unknown_product_returns_404(client, seeded, auth):
    assert (
        client.get("/api/v1/campaigns?product_code=inexistant", headers=auth("lecteur")).status_code
        == 404
    )


def test_manual_prospect_requires_at_least_one_source(client, seeded, auth):
    response = client.post(
        "/api/v1/prospects",
        headers=auth("commercial"),
        json={
            "product_code": "educore",
            "organization_name": "Sans provenance",
            "sources": [],
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_manual_prospect_rejects_a_duplicate(client, seeded, auth):
    payload = {
        "product_code": "educore",
        "organization_name": "Lycée Doublon",
        "website": "https://doublon.example.org",
        "sources": [{"url": "https://doublon.example.org/a-propos"}],
    }
    assert (
        client.post("/api/v1/prospects", headers=auth("commercial"), json=payload).status_code
        == 201
    )
    second = client.post("/api/v1/prospects", headers=auth("commercial"), json=payload)
    assert second.status_code == 409
    assert "existe déjà" in second.json()["detail"]


def test_invalid_campaign_slug_is_rejected(client, seeded, auth):
    response = client.post(
        "/api/v1/campaigns",
        headers=auth("commercial"),
        json={"product_code": "educore", "slug": "Slug Invalide !", "name": "X"},
    )
    assert response.status_code == 422


def test_error_responses_share_a_uniform_shape(client, seeded, auth):
    response = client.post(
        "/api/v1/icps", headers=auth("commercial"), json={"product_code": "educore", "name": "X"}
    )
    body = response.json()
    assert set(body) == {"code", "detail", "hint"}


def test_request_id_header_is_returned(client):
    assert client.get("/health").headers.get("X-Request-ID")


def test_integrations_endpoint_never_exposes_a_secret(client, seeded, auth):
    body = client.get("/api/v1/system/integrations", headers=auth("admin")).json()
    assert body["crm"]["credential_configured"] in (True, False)
    assert "api_token" not in str(body).lower()
    assert body["maintrix"]["access"] == "lecture seule"
    assert body["maintrix"]["authorization_required"] is True
    assert body["educore"]["active"] is False


def test_the_web_role_ladder_matches_the_api():
    """L'interface duplique la hiérarchie des rôles, faute de pouvoir
    partager une constante entre Python et TypeScript.

    Une duplication qui dérive se paie cher ici : un rôle connu d'un seul
    côté verrouille silencieusement l'interface pour qui le porte — il se
    connecte, et tous les boutons disparaissent sans message d'erreur. Le
    même piège que les statuts de purge, qui avaient rendu une métrique
    muette pendant tout un lot.
    """
    import json
    import re
    from pathlib import Path

    from growth_agent.security.rbac import ROLE_RANK

    source = (
        Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "lib" / "auth.tsx"
    ).read_text(encoding="utf-8")
    trouve = re.search(r"const RANK: Record<Role, number> = \{([^}]*)\}", source)
    assert trouve, "RANK introuvable dans apps/web/src/lib/auth.tsx"

    cote_web = json.loads("{" + re.sub(r"(\w+):", r'"\1":', trouve.group(1)).strip() + "}")
    assert cote_web == dict(ROLE_RANK), (
        f"hiérarchie divergente — API {dict(ROLE_RANK)}, interface {cote_web}"
    )


# --- Pipeline commercial : qui peut quoi ----------------------------------


def _prospect_id(session_factory) -> str:
    import uuid as _uuid

    from sqlalchemy import select

    from growth_agent.models.catalog import Product
    from growth_agent.models.prospect import ProspectCandidate

    session = session_factory()
    try:
        produit = session.execute(select(Product).where(Product.code == "educore")).scalar_one()
        prospect = ProspectCandidate(
            product_id=produit.id,
            organization_name="Lycée pipeline",
            dedupe_key=f"domain:{_uuid.uuid4().hex[:8]}.example.org",
            domain=f"{_uuid.uuid4().hex[:8]}.example.org",
            country_code="CM",
            sector="Enseignement secondaire",
            contact_full_name="Contact",
            contact_role="Directeur",
            contact_email=f"{_uuid.uuid4().hex[:6]}@exemple.example.org",
        )
        session.add(prospect)
        session.commit()
        return str(prospect.id)
    finally:
        session.close()


def test_a_reader_sees_the_pipeline_but_does_not_change_it(client, seeded, auth, session_factory):
    lecteur = auth("lecteur")
    assert client.get("/api/v1/opportunities", headers=lecteur).status_code == 200
    assert client.get("/api/v1/next-actions/workload", headers=lecteur).status_code == 200
    assert client.get("/api/v1/cohorts?product_code=educore", headers=lecteur).status_code == 200
    refus = client.post(
        "/api/v1/opportunities",
        headers=lecteur,
        json={"prospect_id": _prospect_id(session_factory), "owner_email": "a@b.example.org"},
    )
    assert refus.status_code == 403


def test_an_operator_runs_the_pipeline_end_to_end(client, seeded, auth, session_factory):
    """Le parcours que l'audit disait absent : ouvrir, consigner une
    démonstration, et voir ce qui est en retard."""
    headers = auth("operateur")

    dossier = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={
            "prospect_id": _prospect_id(session_factory),
            "owner_email": "merveille@techlearn-saem.com",
            "amount": 1500000,
        },
    )
    assert dossier.status_code == 201, dossier.text
    identifiant = dossier.json()["id"]
    assert dossier.json()["currency"] == "XAF"

    compte_rendu = client.post(
        f"/api/v1/opportunities/{identifiant}/demo-report",
        headers=headers,
        json={"held": True, "summary": "Démonstration tenue.", "objections": ["timing"]},
    )
    assert compte_rendu.status_code == 201, compte_rendu.text
    assert compte_rendu.json()["owner_email"] == "merveille@techlearn-saem.com"

    charge = client.get("/api/v1/next-actions/workload", headers=headers).json()
    assert any(entree["owner_email"] == "merveille@techlearn-saem.com" for entree in charge)


def test_losing_a_file_without_a_reason_is_refused_by_the_api(
    client, seeded, auth, session_factory
):
    headers = auth("operateur")
    dossier = client.post(
        "/api/v1/opportunities",
        headers=headers,
        json={"prospect_id": _prospect_id(session_factory), "owner_email": "a@b.example.org"},
    ).json()
    refus = client.post(
        f"/api/v1/opportunities/{dossier['id']}/stage",
        headers=headers,
        json={"stage": "lost"},
    )
    assert refus.status_code == 409
    assert "motif" in refus.json()["detail"]
