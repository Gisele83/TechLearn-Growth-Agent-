"""Adaptateur HubSpot (HTTP).

Écrit d'après la documentation publique de l'API CRM v3. Il n'a PAS été exécuté
contre un compte réel : les noms de propriétés et la forme exacte des réponses
doivent être confrontés à un bac à sable HubSpot avant toute mise en service.
Tant que `GA_CRM_PROVIDER` vaut `mock`, cette classe n'est jamais instanciée.

Comportements implémentés parce qu'ils conditionnent la fiabilité :
  * pagination par curseur `paging.next.after` ;
  * respect du `Retry-After` sur 429 et réessais exponentiels bornés ;
  * idempotence applicative — la clé est écrite dans une propriété dédiée et
    relue avant création, HubSpot n'offrant pas d'en-tête d'idempotence ;
  * coupe-circuit pour ne pas marteler un service en panne.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from growth_agent.connectors.base import (
    CircuitBreaker,
    ConnectorError,
    Page,
    RateLimitError,
    backoff_delays,
)
from growth_agent.connectors.crm.base import (
    CRMAdapter,
    CRMCompany,
    CRMContact,
    CRMDeal,
    CRMNote,
    CRMTask,
)

IDEMPOTENCY_PROPERTY = "growth_agent_idempotency_key"


class HubSpotCRMAdapter(CRMAdapter):
    provider = "hubspot"

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        max_retries: int = 5,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if not api_token:
            raise ConnectorError(
                "Jeton HubSpot absent : l'adaptateur réel ne peut pas être utilisé.",
                provider=self.provider,
            )
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._sleep = sleep
        self._breaker = CircuitBreaker()
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
        )

    # --- Transport -----------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Exécute une requête avec réessais. Les erreurs 4xx non-429 ne sont
        jamais réessayées : elles traduisent une requête invalide, pas un aléa."""
        delays = backoff_delays(self._max_retries)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            self._breaker.ensure_closed(provider=self.provider)
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                self._breaker.record_failure()
                last_error = ConnectorError(
                    f"Erreur réseau HubSpot : {type(exc).__name__}",
                    provider=self.provider,
                    retryable=True,
                )
            else:
                if response.status_code == 429:
                    # Une réponse reçue n'est pas une panne de transport : le
                    # coupe-circuit doit être informé explicitement, sinon un
                    # service en erreur permanente ne le ferait jamais ouvrir.
                    self._breaker.record_failure()
                    retry_after = _retry_after_seconds(
                        response, default=delays[min(attempt, len(delays) - 1)]
                    )
                    last_error = RateLimitError(
                        "Quota HubSpot atteint.",
                        provider=self.provider,
                        retry_after=retry_after,
                    )
                    if attempt < self._max_retries:
                        self._sleep(retry_after)
                        continue
                elif 500 <= response.status_code < 600:
                    self._breaker.record_failure()
                    last_error = ConnectorError(
                        f"HubSpot a renvoyé {response.status_code}.",
                        provider=self.provider,
                        retryable=True,
                    )
                elif response.status_code >= 400:
                    # 4xx hors 429 : la requête est fautive, pas le service.
                    # Le coupe-circuit n'a pas à s'ouvrir, et réessayer ne
                    # ferait que consommer du quota.
                    self._breaker.record_success()
                    raise ConnectorError(
                        f"Requête HubSpot refusée ({response.status_code}).",
                        provider=self.provider,
                        retryable=False,
                    )
                else:
                    self._breaker.record_success()
                    return response.json() if response.content else {}

            if attempt < self._max_retries:
                self._sleep(delays[min(attempt, len(delays) - 1)])

        raise last_error or ConnectorError("Échec HubSpot inconnu.", provider=self.provider)

    # --- Lecture -------------------------------------------------------------
    def list_companies(self, *, cursor: str | None = None, limit: int = 50) -> Page:
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "properties": "name,domain,country,industry,city",
        }
        if cursor:
            params["after"] = cursor
        data = self._request("GET", "/crm/v3/objects/companies", params=params)
        items = [_from_hubspot_company(obj) for obj in data.get("results", [])]
        next_cursor = data.get("paging", {}).get("next", {}).get("after")
        return Page(items=items, next_cursor=next_cursor)

    def find_company_by_domain(self, domain: str) -> CRMCompany | None:
        payload = {
            "filterGroups": [
                {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
            ],
            "properties": ["name", "domain", "country", "industry", "city"],
            "limit": 1,
        }
        data = self._request("POST", "/crm/v3/objects/companies/search", json=payload)
        results = data.get("results", [])
        if not results:
            return None
        raw = _from_hubspot_company(results[0])
        return CRMCompany(
            name=raw["name"],
            domain=raw["domain"],
            country_code=raw["country_code"],
            sector=raw["sector"],
            city=raw["city"],
            remote_id=raw["remote_id"],
        )

    def find_contact_by_email(self, email: str) -> CRMContact | None:
        payload = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "properties": ["email", "firstname", "lastname", "jobtitle", "phone"],
            "limit": 1,
        }
        data = self._request("POST", "/crm/v3/objects/contacts/search", json=payload)
        results = data.get("results", [])
        if not results:
            return None
        props = results[0].get("properties", {})
        full_name = " ".join(
            part for part in (props.get("firstname"), props.get("lastname")) if part
        )
        return CRMContact(
            email=props.get("email", email),
            full_name=full_name,
            role=props.get("jobtitle"),
            phone=props.get("phone"),
            remote_id=results[0].get("id"),
        )

    # --- Écriture ------------------------------------------------------------
    def _find_by_idempotency(self, object_type: str, key: str) -> str | None:
        """HubSpot n'a pas d'idempotence native : on relit la clé que nous avons
        écrite lors d'une tentative précédente. Si la propriété personnalisée
        n'existe pas encore dans le portail, la recherche échoue en 400 et nous
        laissons remonter l'erreur — mieux vaut un échec visible qu'un doublon."""
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": IDEMPOTENCY_PROPERTY,
                            "operator": "EQ",
                            "value": key,
                        }
                    ]
                }
            ],
            "limit": 1,
        }
        data = self._request("POST", f"/crm/v3/objects/{object_type}/search", json=payload)
        results = data.get("results", [])
        return results[0]["id"] if results else None

    def upsert_company(self, company: CRMCompany, *, idempotency_key: str) -> CRMCompany:
        existing = self._find_by_idempotency("companies", idempotency_key)
        properties = {
            "name": company.name,
            "domain": company.domain,
            "country": company.country_code,
            "industry": company.sector,
            "city": company.city,
            IDEMPOTENCY_PROPERTY: idempotency_key,
            **company.properties,
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        if existing:
            self._request(
                "PATCH", f"/crm/v3/objects/companies/{existing}", json={"properties": properties}
            )
            company.remote_id = existing
            return company
        data = self._request("POST", "/crm/v3/objects/companies", json={"properties": properties})
        company.remote_id = data.get("id")
        return company

    def upsert_contact(self, contact: CRMContact, *, idempotency_key: str) -> CRMContact:
        existing = self._find_by_idempotency("contacts", idempotency_key)
        first, _, last = contact.full_name.partition(" ")
        properties = {
            "email": contact.email,
            "firstname": first or None,
            "lastname": last or None,
            "jobtitle": contact.role,
            "phone": contact.phone,
            IDEMPOTENCY_PROPERTY: idempotency_key,
            **contact.properties,
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        if existing:
            self._request(
                "PATCH", f"/crm/v3/objects/contacts/{existing}", json={"properties": properties}
            )
            contact.remote_id = existing
            return contact
        data = self._request("POST", "/crm/v3/objects/contacts", json={"properties": properties})
        contact.remote_id = data.get("id")
        return contact

    def upsert_deal(self, deal: CRMDeal, *, idempotency_key: str) -> CRMDeal:
        existing = self._find_by_idempotency("deals", idempotency_key)
        properties = {
            "dealname": deal.name,
            "dealstage": deal.stage,
            "amount": deal.amount,
            IDEMPOTENCY_PROPERTY: idempotency_key,
            **deal.properties,
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        if existing:
            self._request(
                "PATCH", f"/crm/v3/objects/deals/{existing}", json={"properties": properties}
            )
            deal.remote_id = existing
            return deal
        data = self._request("POST", "/crm/v3/objects/deals", json={"properties": properties})
        deal.remote_id = data.get("id")
        return deal

    def create_task(self, task: CRMTask, *, idempotency_key: str) -> CRMTask:
        existing = self._find_by_idempotency("tasks", idempotency_key)
        if existing:
            task.remote_id = existing
            return task
        properties = {
            "hs_task_subject": task.title,
            "hs_task_body": task.body,
            "hs_timestamp": task.due_at,
            IDEMPOTENCY_PROPERTY: idempotency_key,
        }
        properties = {k: v for k, v in properties.items() if v is not None}
        data = self._request("POST", "/crm/v3/objects/tasks", json={"properties": properties})
        task.remote_id = data.get("id")
        return task

    def create_note(self, note: CRMNote, *, idempotency_key: str) -> CRMNote:
        existing = self._find_by_idempotency("notes", idempotency_key)
        if existing:
            note.remote_id = existing
            return note
        properties = {
            "hs_note_body": note.body,
            IDEMPOTENCY_PROPERTY: idempotency_key,
        }
        data = self._request("POST", "/crm/v3/objects/notes", json={"properties": properties})
        note.remote_id = data.get("id")
        return note

    def healthcheck(self) -> dict[str, Any]:
        try:
            self._request("GET", "/crm/v3/objects/companies", params={"limit": 1})
        except ConnectorError as exc:
            return {"provider": self.provider, "status": "error", "detail": str(exc)}
        return {"provider": self.provider, "status": "ok"}

    def close(self) -> None:
        self._client.close()


def _retry_after_seconds(response: httpx.Response, *, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _from_hubspot_company(obj: dict[str, Any]) -> dict[str, Any]:
    props = obj.get("properties", {})
    return {
        "remote_id": obj.get("id"),
        "name": props.get("name", ""),
        "domain": props.get("domain"),
        "country_code": props.get("country"),
        "sector": props.get("industry"),
        "city": props.get("city"),
        "properties": {},
    }
