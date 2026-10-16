"""Tests d'intégration CRM — contre un vrai portail HubSpot bac à sable.

**Exclus par défaut.** Ils parlent à un service extérieur : une suite qui part
sur le réseau n'est ni reproductible, ni gratuite, ni exécutable hors ligne.
Les lancer explicitement :

    GA_CRM_SANDBOX_TOKEN="pat-…" pytest -m integration tests/test_crm_integration.py

Ce qu'ils vérifient, et que la doublure HTTP ne peut pas vérifier :

* la propriété personnalisée `growth_agent_idempotency_key` existe bien dans
  le portail — sans elle, le filet de secours échoue en 400 ;
* les noms de propriétés (`dealstage`, `hs_task_subject`…) sont les bons ;
* l'API d'associations v4 accepte nos chemins ;
* le délai réel d'indexation de la recherche.

Rien n'est supprimé après coup : un bac à sable se nettoie à la main, et un
test qui supprime des objets dans un CRM est un test qu'on ne veut pas voir
pointé par erreur sur un portail de production.
"""

from __future__ import annotations

import os
import uuid

import pytest

from growth_agent.connectors.crm.base import (
    CRMCompany,
    CRMContact,
    CRMDeal,
    InMemoryIdempotencyStore,
)
from growth_agent.connectors.crm.hubspot import IDEMPOTENCY_PROPERTY, HubSpotCRMAdapter

SANDBOX_TOKEN = os.environ.get("GA_CRM_SANDBOX_TOKEN", "")
SANDBOX_URL = os.environ.get("GA_CRM_SANDBOX_URL", "https://api.hubapi.com")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not SANDBOX_TOKEN,
        reason="GA_CRM_SANDBOX_TOKEN non défini : pas de portail bac à sable",
    ),
]


@pytest.fixture
def sandbox() -> HubSpotCRMAdapter:
    adapter = HubSpotCRMAdapter(
        base_url=SANDBOX_URL,
        api_token=SANDBOX_TOKEN,
        rate_limit_per_second=2.0,
    )
    try:
        yield adapter.with_idempotency_store(InMemoryIdempotencyStore())
    finally:
        adapter.close()


def _marker() -> str:
    """Suffixe unique : deux exécutions ne doivent pas se marcher dessus."""
    return uuid.uuid4().hex[:10]


def test_the_portal_accepts_our_idempotency_property(sandbox):
    """Si la propriété personnalisée n'a pas été créée dans le portail, la
    recherche échoue en 400 — et c'est tout le filet de secours qui tombe."""
    marker = _marker()
    company = sandbox.upsert_company(
        CRMCompany(name=f"Recette TechLearn {marker}", domain=f"{marker}.example.org"),
        idempotency_key=f"recette-societe-{marker}",
    )
    assert company.remote_id

    found = sandbox._search_by_idempotency("companies", f"recette-societe-{marker}")
    assert found == company.remote_id, (
        f"la propriété {IDEMPOTENCY_PROPERTY} n'est pas exploitable dans ce portail"
    )


def test_an_upsert_replayed_against_the_real_portal_creates_one_object(sandbox):
    marker = _marker()
    key = f"recette-rejeu-{marker}"
    payload = CRMCompany(name=f"Rejeu {marker}", domain=f"rejeu-{marker}.example.org")

    first = sandbox.upsert_company(payload, idempotency_key=key)
    second = sandbox.upsert_company(payload, idempotency_key=key)
    assert first.remote_id == second.remote_id


def test_associations_are_accepted_by_the_real_api(sandbox):
    marker = _marker()
    company = sandbox.upsert_company(
        CRMCompany(name=f"Assoc {marker}", domain=f"assoc-{marker}.example.org"),
        idempotency_key=f"assoc-societe-{marker}",
    )
    contact = sandbox.upsert_contact(
        CRMContact(
            email=f"contact-{marker}@exemple.example.org",
            full_name="Contact Recette",
            company_remote_id=company.remote_id,
        ),
        idempotency_key=f"assoc-contact-{marker}",
    )
    deal = sandbox.upsert_deal(
        CRMDeal(
            name=f"Affaire {marker}",
            stage="appointmentscheduled",
            company_remote_id=company.remote_id,
            contact_remote_id=contact.remote_id,
        ),
        idempotency_key=f"assoc-affaire-{marker}",
    )
    # Pas d'assertion sur le contenu : l'absence d'exception suffit. Un chemin
    # d'association invalide remonterait en 4xx, non réessayé.
    assert deal.remote_id


def test_the_healthcheck_reaches_the_portal(sandbox):
    assert sandbox.healthcheck()["status"] == "ok"
