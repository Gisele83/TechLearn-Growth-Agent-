"""Parcours commercial complet, de bout en bout, par l'API.

prospect → qualification → brouillon → approbation → envoi → réponse →
démonstration → pilote, plus les pannes de connecteurs.

C'est le test qui vaut recette : s'il passe, le système tient ses promesses
sans qu'aucun message ne soit parti pour de vrai.
"""

from __future__ import annotations

import pytest

from growth_agent.services.opt_out_language import mentions_opt_out


@pytest.fixture
def journey(client, seeded, auth):
    """Contexte partagé : rôles et campagne EduCore."""
    commercial = auth("commercial")
    approbateur = auth("approbateur")
    campaign = client.get("/api/v1/campaigns?product_code=educore", headers=commercial).json()[0]
    return {
        "client": client,
        "commercial": commercial,
        "approbateur": approbateur,
        "admin": auth("admin"),
        "lecteur": auth("lecteur"),
        "campaign": campaign,
    }


def test_full_journey_from_discovery_to_pilot(journey):
    client = journey["client"]
    com = journey["commercial"]
    appr = journey["approbateur"]
    campaign = journey["campaign"]

    # 1. Découverte -----------------------------------------------------------
    discovery = client.post(
        "/api/v1/prospects/discover",
        headers=com,
        json={"product_code": "educore", "campaign_id": campaign["id"], "limit": 10},
    )
    assert discovery.status_code == 200
    assert discovery.json()["created"] > 0

    prospects = client.get("/api/v1/prospects?product_code=educore", headers=com).json()["items"]
    assert prospects

    # Chaque prospect découvert porte au moins une source datée.
    for item in prospects:
        detail = client.get(f"/api/v1/prospects/{item['id']}", headers=com).json()
        assert detail["sources"], f"{item['organization_name']} sans provenance"
        assert all(s["url"].startswith("https://") for s in detail["sources"])

    # 2. Qualification --------------------------------------------------------
    for item in prospects:
        assessment = client.post(f"/api/v1/prospects/{item['id']}/qualify", headers=com, json={})
        assert assessment.status_code == 200
        body = assessment.json()
        assert len(body["criteria"]) == 5
        assert body["rationale"]

    prospects = client.get(
        "/api/v1/prospects?product_code=educore&verification_status=verified", headers=com
    ).json()["items"]
    target = next(p for p in prospects if p["contact_email"])

    # 3. Rédaction ------------------------------------------------------------
    template = client.get("/api/v1/templates?product_code=educore", headers=com).json()[0]
    draft = client.post(
        "/api/v1/drafts",
        headers=com,
        json={"prospect_id": target["id"], "template_id": template["id"]},
    ).json()
    assert draft["status"] == "draft"
    # Le message doit offrir un moyen de s'opposer. Lequel dépend de la
    # configuration : « répondez STOP » n'a de sens que si une boîte reçoit
    # les réponses, sinon c'est le lien de désinscription.
    assert mentions_opt_out(draft["body"])

    # 4. L'envoi est refusé sans approbation ----------------------------------
    premature = client.post(f"/api/v1/drafts/{draft['id']}/send", headers=com).json()
    assert premature["sent"] is False
    assert premature["reason_code"] == "not_approved"

    # 5. Approbation par une autre personne ------------------------------------
    approval = client.post("/api/v1/approvals", headers=com, json={"draft_id": draft["id"]}).json()
    assert (
        client.post(
            f"/api/v1/approvals/{approval['id']}/decision", headers=com, json={"approve": True}
        ).status_code
        == 409
    ), "l'auteur ne peut pas s'auto-approuver"

    decided = client.post(
        f"/api/v1/approvals/{approval['id']}/decision",
        headers=appr,
        json={"approve": True, "comment": "Message relu et conforme."},
    )
    assert decided.status_code == 200
    assert decided.json()["decision"] == "approved"

    # 6. Envoi — simulé --------------------------------------------------------
    sent = client.post(f"/api/v1/drafts/{draft['id']}/send", headers=com).json()
    assert sent["sent"] is True
    assert sent["simulated"] is True

    refreshed = client.get(f"/api/v1/prospects/{target['id']}", headers=com).json()
    assert refreshed["stage"] == "contacted"

    # 7. Réponse favorable -----------------------------------------------------
    reply = client.post(
        "/api/v1/replies/analyze",
        headers=com,
        json={
            "prospect_id": target["id"],
            "reply_text": "Bonjour, je suis intéressé, une démonstration serait utile.",
        },
    ).json()
    assert reply["intent"] == "interested"
    assert reply["opted_out"] is False

    # 8. KPI recalculés --------------------------------------------------------
    # Le parcours s'est déroulé en simulation de bout en bout. Par défaut, les
    # KPI ne comptent donc personne comme contacté : c'est exactement ce qu'on
    # veut lire, et c'est annoncé dans les notes plutôt que laissé deviner.
    kpis = client.get("/api/v1/kpis?product_code=educore", headers=journey["lecteur"]).json()[0]
    assert kpis["funnel"]["contacted"] == 0
    assert kpis["simulated_total"] == 1
    assert any("simulé" in note for note in kpis["notes"])

    # Les mêmes chiffres, répétitions comprises, sur demande explicite.
    with_rehearsals = client.get(
        "/api/v1/kpis?product_code=educore&include_simulated=true", headers=journey["lecteur"]
    ).json()[0]
    assert with_rehearsals["funnel"]["contacted"] == 1
    assert with_rehearsals["funnel"]["replied"] == 1
    assert with_rehearsals["reply_rate"] == 1.0

    # 9. Suivi de pilote (lecture seule, simulée) ------------------------------
    sync = client.post("/api/v1/pilots/educore/sync", headers=com).json()
    assert sync["read_only"] is True
    assert sync["adapter_mode"] == "mock"
    assert sync["imported"] > 0

    # 10. Journal d'audit ------------------------------------------------------
    actions = [
        event["action"]
        for event in client.get("/api/v1/audit?limit=300", headers=journey["lecteur"]).json()
    ]
    for expected in (
        "market_scout.discovered",
        "lead_intelligence.qualified",
        "outreach_composer.drafted",
        "approval.requested",
        "approval.approved",
        # Le parcours est simulé : l'audit le dit, au lieu de laisser croire
        # qu'un message a été expédié.
        "outreach.sent_simulated",
        "outreach.send_blocked",
    ):
        assert expected in actions, f"action absente du journal : {expected}"


def test_opt_out_during_the_journey_stops_everything(journey):
    client = journey["client"]
    com = journey["commercial"]
    appr = journey["approbateur"]
    campaign = journey["campaign"]

    client.post(
        "/api/v1/prospects/discover",
        headers=com,
        json={"product_code": "educore", "campaign_id": campaign["id"], "limit": 10},
    )
    prospects = client.get("/api/v1/prospects?product_code=educore", headers=com).json()["items"]
    for item in prospects:
        client.post(f"/api/v1/prospects/{item['id']}/qualify", headers=com, json={})
    verified = client.get(
        "/api/v1/prospects?product_code=educore&verification_status=verified", headers=com
    ).json()["items"]
    target = next(p for p in verified if p["contact_email"])

    template = client.get("/api/v1/templates?product_code=educore", headers=com).json()[0]
    draft = client.post(
        "/api/v1/drafts",
        headers=com,
        json={"prospect_id": target["id"], "template_id": template["id"]},
    ).json()
    approval = client.post("/api/v1/approvals", headers=com, json={"draft_id": draft["id"]}).json()
    client.post(
        f"/api/v1/approvals/{approval['id']}/decision", headers=appr, json={"approve": True}
    )
    client.post(f"/api/v1/drafts/{draft['id']}/send", headers=com)

    # Le prospect demande à ne plus être contacté.
    reply = client.post(
        "/api/v1/replies/analyze",
        headers=com,
        json={"prospect_id": target["id"], "reply_text": "STOP, ne plus me contacter."},
    ).json()
    assert reply["opted_out"] is True

    # Toute nouvelle rédaction est désormais refusée.
    refused = client.post(
        "/api/v1/drafts",
        headers=com,
        json={"prospect_id": target["id"], "template_id": template["id"]},
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "composition_refused"

    # Et l'opposition figure dans la liste.
    values = [
        entry["normalized_value"]
        for entry in client.get("/api/v1/suppressions", headers=com).json()
    ]
    assert target["contact_email"] in values


def test_the_two_products_stay_separated(journey):
    client = journey["client"]
    com = journey["commercial"]

    for code in ("educore", "maintrix"):
        campaign = client.get(f"/api/v1/campaigns?product_code={code}", headers=com).json()[0]
        client.post(
            "/api/v1/prospects/discover",
            headers=com,
            json={"product_code": code, "campaign_id": campaign["id"], "limit": 10},
        )

    educore = client.get("/api/v1/prospects?product_code=educore", headers=com).json()
    maintrix = client.get("/api/v1/prospects?product_code=maintrix", headers=com).json()
    assert educore["meta"]["total"] > 0
    assert maintrix["meta"]["total"] > 0

    educore_ids = {p["id"] for p in educore["items"]}
    maintrix_ids = {p["id"] for p in maintrix["items"]}
    assert educore_ids.isdisjoint(maintrix_ids)

    kpis = {k["product_code"]: k for k in client.get("/api/v1/kpis", headers=com).json()}
    assert set(kpis) == {"educore", "maintrix"}


def test_kpis_are_recomputed_not_stored(journey):
    """Deux appels successifs donnent le même résultat, et ce résultat suit
    les événements : rien n'est mis en cache."""
    client = journey["client"]
    com = journey["commercial"]

    def funnel():
        return client.get("/api/v1/kpis?product_code=educore", headers=com).json()[0]["funnel"]

    before = funnel()
    assert before == funnel()

    client.post(
        "/api/v1/prospects/discover",
        headers=com,
        json={"product_code": "educore", "campaign_id": journey["campaign"]["id"], "limit": 10},
    )
    assert funnel()["discovered"] > before["discovered"]


def test_rates_are_null_when_there_is_no_basis(journey):
    """Pas d'envoi = pas de taux. On n'affiche pas 0 %, qui se lirait comme un
    échec plutôt que comme une absence de mesure."""
    client = journey["client"]
    kpis = client.get("/api/v1/kpis?product_code=maintrix", headers=journey["lecteur"]).json()[0]
    assert kpis["reply_rate"] is None
    assert kpis["demo_rate"] is None
    assert any("ne sont pas calculables" in note for note in kpis["notes"])


def test_editing_an_approved_draft_forces_a_new_approval(journey):
    client = journey["client"]
    com = journey["commercial"]
    appr = journey["approbateur"]

    client.post(
        "/api/v1/prospects/discover",
        headers=com,
        json={"product_code": "educore", "campaign_id": journey["campaign"]["id"], "limit": 10},
    )
    prospects = client.get("/api/v1/prospects?product_code=educore", headers=com).json()["items"]
    for item in prospects:
        client.post(f"/api/v1/prospects/{item['id']}/qualify", headers=com, json={})
    verified = client.get(
        "/api/v1/prospects?product_code=educore&verification_status=verified", headers=com
    ).json()["items"]
    target = next(p for p in verified if p["contact_email"])

    draft = client.post("/api/v1/drafts", headers=com, json={"prospect_id": target["id"]}).json()
    approval = client.post("/api/v1/approvals", headers=com, json={"draft_id": draft["id"]}).json()
    client.post(
        f"/api/v1/approvals/{approval['id']}/decision", headers=appr, json={"approve": True}
    )

    edited = client.patch(
        f"/api/v1/drafts/{draft['id']}",
        headers=com,
        json={"body": "Texte entièrement réécrit après validation. Répondez STOP pour refuser."},
    ).json()
    assert edited["status"] == "draft"

    blocked = client.post(f"/api/v1/drafts/{draft['id']}/send", headers=com).json()
    assert blocked["sent"] is False
    assert blocked["reason_code"] == "not_approved"


def test_a_sent_message_can_no_longer_be_edited(journey):
    client = journey["client"]
    com = journey["commercial"]
    appr = journey["approbateur"]

    client.post(
        "/api/v1/prospects/discover",
        headers=com,
        json={"product_code": "educore", "campaign_id": journey["campaign"]["id"], "limit": 10},
    )
    prospects = client.get("/api/v1/prospects?product_code=educore", headers=com).json()["items"]
    for item in prospects:
        client.post(f"/api/v1/prospects/{item['id']}/qualify", headers=com, json={})
    verified = client.get(
        "/api/v1/prospects?product_code=educore&verification_status=verified", headers=com
    ).json()["items"]
    target = next(p for p in verified if p["contact_email"])

    draft = client.post("/api/v1/drafts", headers=com, json={"prospect_id": target["id"]}).json()
    approval = client.post("/api/v1/approvals", headers=com, json={"draft_id": draft["id"]}).json()
    client.post(
        f"/api/v1/approvals/{approval['id']}/decision", headers=appr, json={"approve": True}
    )
    client.post(f"/api/v1/drafts/{draft['id']}/send", headers=com)

    response = client.patch(
        f"/api/v1/drafts/{draft['id']}", headers=com, json={"subject": "Trop tard"}
    )
    assert response.status_code == 409
