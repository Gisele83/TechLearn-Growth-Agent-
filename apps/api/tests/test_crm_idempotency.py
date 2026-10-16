"""Idempotence CRM, débit et associations.

Le fil conducteur : **ne jamais créer deux fois le même objet chez un client**,
même quand le CRM ment sur ce qu'il contient.
"""

from __future__ import annotations

import uuid

import pytest

from growth_agent.connectors.crm.base import (
    CRMCompany,
    CRMContact,
    CRMDeal,
    CRMNote,
    CRMTask,
    InMemoryIdempotencyStore,
    NullIdempotencyStore,
)
from growth_agent.connectors.crm.hubspot import IDEMPOTENCY_PROPERTY, HubSpotCRMAdapter
from growth_agent.models.prospect import CRMMapping
from growth_agent.services.crm_mapping import DatabaseIdempotencyStore, remember_mapping
from hubspot_double import HubSpotDouble


def _adapter(portal: HubSpotDouble, store=None, **kwargs) -> HubSpotCRMAdapter:
    adapter = HubSpotCRMAdapter(
        base_url="https://portail.test",
        api_token="jeton-de-test",
        client=portal.client(),
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return adapter.with_idempotency_store(store) if store is not None else adapter


# --- Le défaut : l'index de recherche est à cohérence différée -------------


def test_without_local_memory_an_immediate_retry_creates_a_duplicate():
    """Démonstration du défaut, pour que la correction ait un point de
    comparaison. Sans mémoire locale, l'adaptateur ne dispose que de l'index
    HubSpot — qui ne connaît pas encore l'objet qu'il vient de créer."""
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, NullIdempotencyStore())

    company = CRMCompany(name="Agro Douala", domain="agro.example.org")
    adapter.upsert_company(company, idempotency_key="k-1")
    adapter.upsert_company(company, idempotency_key="k-1")

    assert portal.count("companies") == 2, "le défaut n'est plus reproduit"


def test_local_memory_prevents_the_duplicate(session):
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))

    first = adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )
    second = adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )

    assert portal.count("companies") == 1
    assert first.remote_id == second.remote_id


def test_memory_survives_a_restart(session_factory):
    """Le cas qui compte : le processus meurt après la création, et la reprise
    repart d'une session neuve. La correspondance est en base, pas en
    mémoire vive."""
    portal = HubSpotDouble(index_delay=None)

    first_session = session_factory()
    try:
        adapter = _adapter(portal, DatabaseIdempotencyStore(first_session))
        created = adapter.upsert_company(
            CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
        )
        first_session.commit()
    finally:
        first_session.close()

    after_restart = session_factory()
    try:
        adapter = _adapter(portal, DatabaseIdempotencyStore(after_restart))
        again = adapter.upsert_company(
            CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
        )
    finally:
        after_restart.close()

    assert portal.count("companies") == 1
    assert again.remote_id == created.remote_id


def test_a_lost_local_memory_falls_back_to_the_remote_search():
    """Filet de secours : base restaurée, mémoire vide, mais l'objet existe
    déjà côté CRM et son index l'a rattrapé."""
    portal = HubSpotDouble(index_delay=0)
    adapter = _adapter(portal, InMemoryIdempotencyStore())
    created = adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )

    amnesiac = _adapter(portal, InMemoryIdempotencyStore())
    again = amnesiac.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )

    assert portal.count("companies") == 1
    assert again.remote_id == created.remote_id


def test_the_fallback_search_can_be_turned_off():
    portal = HubSpotDouble(index_delay=0)
    adapter = _adapter(portal, InMemoryIdempotencyStore(), search_fallback=False)
    adapter.upsert_company(CRMCompany(name="Agro"), idempotency_key="k-1")
    assert portal.searches == []


# --- Quota consommé --------------------------------------------------------


def test_a_repeated_sync_costs_one_call_instead_of_two(session):
    """Avant, chaque écriture coûtait une recherche puis une écriture. La
    mémoire locale supprime la recherche dès la deuxième synchronisation."""
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))
    company = CRMCompany(name="Agro Douala", domain="agro.example.org")

    adapter.upsert_company(company, idempotency_key="k-1")
    first_round = len(portal.calls)
    portal.calls.clear()

    adapter.upsert_company(company, idempotency_key="k-1")
    second_round = len(portal.calls)

    assert first_round == 2  # recherche (filet) + création
    assert second_round == 1  # mise à jour seule
    assert portal.searches == []


def test_a_note_already_written_is_not_rewritten(session):
    """Une note est la trace d'un moment, pas une fiche qu'on tient à jour :
    la réécrire effacerait ce qu'elle disait."""
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))

    adapter.create_note(CRMNote(body="Premier échange, intéressé."), idempotency_key="n-1")
    portal.calls.clear()
    adapter.create_note(CRMNote(body="Texte réécrit par erreur"), idempotency_key="n-1")

    assert portal.count("notes") == 1
    assert portal.created("notes")[0]["properties"]["hs_note_body"] == (
        "Premier échange, intéressé."
    )
    assert [c for c in portal.calls if c.startswith("PATCH")] == []


# --- Associations ----------------------------------------------------------


def test_a_deal_is_attached_to_its_company_and_its_contact(session):
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))

    company = adapter.upsert_company(CRMCompany(name="Agro Douala"), idempotency_key="c-1")
    contact = adapter.upsert_contact(
        CRMContact(
            email="directeur@exemple.example.org",
            full_name="Awa Njoya",
            company_remote_id=company.remote_id,
        ),
        idempotency_key="p-1",
    )
    deal = adapter.upsert_deal(
        CRMDeal(
            name="Pilote EduCore",
            stage="qualified",
            company_remote_id=company.remote_id,
            contact_remote_id=contact.remote_id,
        ),
        idempotency_key="d-1",
    )

    assert ("contacts", contact.remote_id, "companies", company.remote_id) in portal.associations
    assert ("deals", deal.remote_id, "companies", company.remote_id) in portal.associations
    assert ("deals", deal.remote_id, "contacts", contact.remote_id) in portal.associations


def test_a_task_is_attached_to_its_contact(session):
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))
    task = adapter.create_task(
        CRMTask(title="Relancer", contact_remote_id="42"), idempotency_key="t-1"
    )
    assert ("tasks", task.remote_id, "contacts", "42") in portal.associations


def test_nothing_is_associated_to_a_missing_counterpart(session):
    """Une association vers un identifiant absent serait une requête vouée au
    404 : on ne la tente pas."""
    portal = HubSpotDouble(index_delay=None)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))
    adapter.upsert_deal(CRMDeal(name="Sans rattachement", stage="new"), idempotency_key="d-2")
    assert portal.associations == []
    assert portal.calls_to("/associations/") == []


# --- Débit -----------------------------------------------------------------


def test_the_configured_rate_limit_is_actually_applied():
    """Le réglage ne doit pas être décoratif : il l'était, référencé nulle
    part dans le code."""
    waits: list[float] = []
    portal = HubSpotDouble(index_delay=0)
    adapter = HubSpotCRMAdapter(
        base_url="https://portail.test",
        api_token="jeton-de-test",
        client=portal.client(),
        sleep=waits.append,
        rate_limit_per_second=2.0,
    )
    for _ in range(6):
        adapter.list_companies(limit=1)

    assert any(wait > 0 for wait in waits), "aucune attente : le limiteur ne s'applique pas"
    # 2 appels par seconde, capacité 2 : les deux premiers passent, les
    # suivants attendent environ une demi-seconde chacun.
    assert sum(waits) == pytest.approx(2.0, abs=0.2)


def test_retries_also_consume_the_quota():
    """Un service qui répond 429 est celui qu'il ne faut surtout pas
    marteler : chaque réessai passe par le limiteur."""
    waits: list[float] = []
    portal = HubSpotDouble(index_delay=0, rate_limited_calls=2, retry_after="0")
    adapter = HubSpotCRMAdapter(
        base_url="https://portail.test",
        api_token="jeton-de-test",
        client=portal.client(),
        sleep=waits.append,
        rate_limit_per_second=1.0,
    )
    adapter.list_companies(limit=1)
    assert len(portal.calls) == 3  # deux refus puis un succès
    assert sum(waits) > 0


# --- La table de correspondance -------------------------------------------


def test_several_tasks_for_one_prospect_no_longer_collide(session):
    """Un prospect reçoit plusieurs tâches — une relance, puis une
    démonstration. L'ancienne contrainte d'unicité le rendait impossible."""
    prospect_id = uuid.uuid4()
    for index, remote in enumerate(("task-1", "task-2", "task-3")):
        remember_mapping(
            session,
            provider="hubspot",
            object_type="task",
            local_id=prospect_id,
            remote_id=remote,
            idempotency_key=f"t-{index}",
        )
    session.commit()
    assert session.query(CRMMapping).filter(CRMMapping.local_id == prospect_id).count() == 3


def test_the_same_key_never_produces_two_mappings(session):
    store = DatabaseIdempotencyStore(session)
    for remote in ("a", "b"):
        store.remember(
            provider="hubspot", object_type="company", idempotency_key="k-1", remote_id=remote
        )
    session.commit()
    rows = session.query(CRMMapping).filter(CRMMapping.idempotency_key == "k-1").all()
    assert len(rows) == 1
    assert rows[0].remote_id == "a"


def test_an_unknown_key_is_simply_unknown(session):
    store = DatabaseIdempotencyStore(session)
    assert store.lookup(provider="hubspot", object_type="company", idempotency_key="jamais") is None
    assert store.lookup(provider="hubspot", object_type="company", idempotency_key="") is None


def test_the_idempotency_property_is_written_on_the_remote_object(session):
    """Le filet de secours en dépend : sans cette propriété, une mémoire
    locale perdue ne pourrait plus rien retrouver."""
    portal = HubSpotDouble(index_delay=0)
    adapter = _adapter(portal, DatabaseIdempotencyStore(session))
    adapter.upsert_company(CRMCompany(name="Agro Douala"), idempotency_key="k-9")
    assert portal.created("companies")[0]["properties"][IDEMPOTENCY_PROPERTY] == "k-9"
