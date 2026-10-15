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
