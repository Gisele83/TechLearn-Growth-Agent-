"""CRM : pagination, quota, idempotence, reprise."""

from __future__ import annotations

import httpx
import pytest

from growth_agent.connectors.base import ConnectorError, RateLimitError
from growth_agent.connectors.crm.base import CRMCompany, CRMContact
from growth_agent.connectors.crm.hubspot import IDEMPOTENCY_PROPERTY, HubSpotCRMAdapter
from growth_agent.connectors.crm.mock import MockCRMAdapter


def test_pagination_walks_every_page_without_repeating():
    adapter = MockCRMAdapter(page_size=10)
    for index in range(25):
        adapter.seed_company(name=f"Société {index}", domain=f"s{index}.example.org")

    seen = list(adapter.iter_companies(page_size=10))
    assert len(seen) == 25
    assert len({c["remote_id"] for c in seen}) == 25


def test_pagination_stops_if_the_cursor_repeats():
    """Garde-fou : un fournisseur qui renvoie toujours le même curseur ne doit
    pas faire boucler l'import indéfiniment."""
    from growth_agent.connectors.base import Page

    class StuckAdapter(MockCRMAdapter):
        def list_companies(self, *, cursor=None, limit=50):
            return Page(items=[{"remote_id": "x"}], next_cursor="toujours-le-meme")

    assert len(list(StuckAdapter().iter_companies())) <= 2


def test_rate_limit_is_raised_as_a_retryable_error():
    adapter = MockCRMAdapter(rate_limit_after=2)
    adapter.list_companies()
    adapter.list_companies()
    with pytest.raises(RateLimitError) as excinfo:
        adapter.list_companies()
    assert excinfo.value.retryable is True


def test_idempotent_upsert_creates_a_single_company():
    adapter = MockCRMAdapter()
    first = adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )
    second = adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )
    assert first.remote_id == second.remote_id
    assert adapter.company_count == 1


def test_same_domain_different_key_updates_instead_of_duplicating():
    adapter = MockCRMAdapter()
    adapter.upsert_company(
        CRMCompany(name="Agro Douala", domain="agro.example.org"), idempotency_key="k-1"
    )
    adapter.upsert_company(
        CRMCompany(name="Agro Douala SA", domain="agro.example.org"), idempotency_key="k-2"
    )
    assert adapter.company_count == 1


def test_contact_upsert_is_idempotent_on_email():
    adapter = MockCRMAdapter()
    adapter.upsert_contact(CRMContact(email="a@x.example.org"), idempotency_key="c-1")
    adapter.upsert_contact(
        CRMContact(email="A@X.example.org", full_name="Alice"), idempotency_key="c-2"
    )
    assert adapter.contact_count == 1


def test_find_company_by_domain_is_case_insensitive():
    adapter = MockCRMAdapter()
    adapter.seed_company(name="X", domain="Mon-Domaine.example.org")
    assert adapter.find_company_by_domain("mon-domaine.example.org") is not None


# --- Adaptateur HubSpot, piloté par un transport simulé ----------------------


def _hubspot(handler, *, max_retries=3):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.hubapi.test")
    return HubSpotCRMAdapter(
        base_url="https://api.hubapi.test",
        api_token="jeton-de-test",
        max_retries=max_retries,
        client=client,
        sleep=lambda _seconds: None,
    )


def test_hubspot_requires_a_token():
    with pytest.raises(ConnectorError):
        HubSpotCRMAdapter(base_url="https://api.hubapi.test", api_token="")


def test_hubspot_retries_after_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"results": [], "paging": {}})

    adapter = _hubspot(handler)
    page = adapter.list_companies()
    assert calls["n"] == 2
    assert page.items == []


def test_hubspot_gives_up_after_max_retries():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"})

    with pytest.raises(RateLimitError):
        _hubspot(handler, max_retries=2).list_companies()


def test_hubspot_does_not_retry_a_client_error():
    """Un 400 traduit une requête invalide : réessayer ne ferait que perdre
    du quota."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"message": "propriété inconnue"})

    with pytest.raises(ConnectorError) as excinfo:
        _hubspot(handler).list_companies()
    assert calls["n"] == 1
    assert excinfo.value.retryable is False


def test_hubspot_follows_the_pagination_cursor():
    pages = {
        None: {"results": [_hs_company("1", "a.example.org")], "paging": {"next": {"after": "C2"}}},
        "C2": {"results": [_hs_company("2", "b.example.org")]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        return httpx.Response(200, json=pages[after])

    adapter = _hubspot(handler)
    assert len(list(adapter.iter_companies())) == 2


def test_hubspot_writes_an_idempotency_property_and_reuses_it():
    seen_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": []})
        seen_bodies.append(request.content.decode())
        return httpx.Response(201, json={"id": "42"})

    adapter = _hubspot(handler)
    company = adapter.upsert_company(
        CRMCompany(name="Agro", domain="agro.example.org"), idempotency_key="idem-abc"
    )
    assert company.remote_id == "42"
    assert IDEMPOTENCY_PROPERTY in seen_bodies[0]
    assert "idem-abc" in seen_bodies[0]


def test_hubspot_patches_instead_of_recreating_when_key_is_known():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"id": "77"}]})
        methods.append(request.method)
        return httpx.Response(200, json={"id": "77"})

    adapter = _hubspot(handler)
    company = adapter.upsert_company(CRMCompany(name="Agro"), idempotency_key="idem-xyz")
    assert company.remote_id == "77"
    assert methods == ["PATCH"]


def test_circuit_breaker_opens_after_repeated_failures():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    adapter = _hubspot(handler, max_retries=1)
    for _ in range(3):
        with pytest.raises(ConnectorError):
            adapter.list_companies()
    with pytest.raises(ConnectorError, match="Coupe-circuit"):
        adapter.list_companies()


def _hs_company(remote_id: str, domain: str) -> dict:
    return {"id": remote_id, "properties": {"name": f"Société {remote_id}", "domain": domain}}
