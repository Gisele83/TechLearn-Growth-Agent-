"""Adaptateur HubSpot (HTTP).

Écrit d'après la documentation publique de l'API CRM v3. Il n'a PAS été exécuté
contre un compte réel : les noms de propriétés et la forme exacte des réponses
doivent être confrontés à un bac à sable HubSpot avant toute mise en service.
Tant que `GA_CRM_PROVIDER` vaut `mock`, cette classe n'est jamais instanciée.

Comportements implémentés parce qu'ils conditionnent la fiabilité :
  * pagination par curseur `paging.next.after` ;
  * respect du `Retry-After` sur 429 et réessais exponentiels bornés ;
  * idempotence tenue **localement** — l'index de recherche HubSpot est à
    cohérence différée, s'y fier pour savoir ce qu'on a déjà créé produit des
    doublons ; la recherche distante ne sert plus que de filet ;
  * associations v4 entre les objets créés, sans quoi une affaire n'apparaît
    pas dans la fiche de son entreprise ;
  * limiteur de débit à jetons, appliqué à chaque requête ;
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
    TokenBucket,
    backoff_delays,
)
from growth_agent.connectors.crm.base import (
    CRMAdapter,
    CRMCompany,
    CRMContact,
    CRMDeal,
    CRMIdempotencyStore,
    CRMNote,
    CRMTask,
    NullIdempotencyStore,
)

IDEMPOTENCY_PROPERTY = "growth_agent_idempotency_key"

#: Types d'objets HubSpot, tels qu'ils apparaissent dans les chemins d'API et
#: dans les associations v4.
COMPANIES = "companies"
CONTACTS = "contacts"
DEALS = "deals"
TASKS = "tasks"
NOTES = "notes"


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
        rate_limit_per_second: float = 4.0,
        rate_limiter: TokenBucket | None = None,
        idempotency_store: CRMIdempotencyStore | None = None,
        search_fallback: bool = True,
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
        # Le limiteur est partagé par toutes les vues de cet adaptateur : un
        # quota est une ressource globale, pas une ressource par requête HTTP.
        self._limiter = rate_limiter or TokenBucket(rate_limit_per_second, sleep=sleep)
        self._store: CRMIdempotencyStore = idempotency_store or NullIdempotencyStore()
        # Le filet coûte un appel de recherche à chaque première écriture, sur
        # des endpoints dont le quota est plus bas que le quota général. Il se
        # désactive une fois la mémoire locale jugée fiable — mais il reste
        # actif par défaut : un doublon dans le CRM d'un client se voit, et se
        # paie plus cher qu'un appel d'API.
        self._search_fallback = search_fallback
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            },
        )

    # --- Mémoire d'idempotence ----------------------------------------------
    def with_idempotency_store(self, store: CRMIdempotencyStore) -> HubSpotCRMAdapter:
        """Vue de cet adaptateur adossée à `store`.

        Client HTTP, coupe-circuit et limiteur sont partagés — ce sont des
        ressources globales. Seule la mémoire diffère, parce qu'elle appartient
        à une transaction.
        """
        view = object.__new__(HubSpotCRMAdapter)
        view.__dict__.update(self.__dict__)
        view._store = store
        return view

    # --- Transport -----------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Exécute une requête avec réessais. Les erreurs 4xx non-429 ne sont
        jamais réessayées : elles traduisent une requête invalide, pas un aléa."""
        delays = backoff_delays(self._max_retries)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            self._breaker.ensure_closed(provider=self.provider)
            # Chaque tentative consomme un jeton, les réessais compris : un
            # service qui répond 429 est précisément celui qu'il ne faut pas
            # marteler.
            self._limiter.acquire()
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
    def _resolve_existing(self, object_type: str, key: str) -> str | None:
        """Identifiant déjà créé pour cette clé, s'il en existe un.

        La mémoire locale est consultée **en premier**, et c'est l'essentiel :
        elle est immédiatement cohérente. L'index de recherche HubSpot, lui, ne
        l'est pas — un objet créé il y a deux secondes n'y figure pas encore, et
        une reprise dans cette fenêtre créerait un doublon.

        La recherche distante reste comme filet, pour le cas où la mémoire
        locale aurait été perdue ou n'aurait jamais vu cette écriture.
        """
        known = self._store.lookup(
            provider=self.provider, object_type=object_type, idempotency_key=key
        )
        if known:
            return known
        if not self._search_fallback:
            return None
        return self._search_by_idempotency(object_type, key)

    def _search_by_idempotency(self, object_type: str, key: str) -> str | None:
        """Filet de secours : relire la clé que nous avons écrite dans une
        propriété dédiée. Si cette propriété n'existe pas encore dans le
        portail, la recherche échoue en 400 et nous laissons remonter l'erreur
        — mieux vaut un échec visible qu'un doublon silencieux."""
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
        found = results[0]["id"] if results else None
        if found:
            self._store.remember(
                provider=self.provider,
                object_type=object_type,
                idempotency_key=key,
                remote_id=found,
            )
        return found

    def _created(self, object_type: str, key: str, remote_id: str | None) -> str | None:
        """Mémorise l'objet qui vient d'être créé, avant tout autre appel.

        L'ordre compte : si le processus meurt après la création mais avant
        l'écriture locale, la reprise ne saura pas que l'objet existe et en
        créera un second.
        """
        if remote_id:
            self._store.remember(
                provider=self.provider,
                object_type=object_type,
                idempotency_key=key,
                remote_id=remote_id,
            )
        return remote_id

    def associate(self, *, from_type: str, from_id: str, to_type: str, to_id: str) -> None:
        """Associe deux objets avec le type d'association par défaut (API v4).

        Sans cela, une affaire n'apparaît pas dans la fiche de son entreprise
        et une note n'apparaît pas dans celle de son contact : l'équipe
        commerciale verrait des objets orphelins.

        Une association est idempotente côté HubSpot — la rejouer ne crée pas
        de doublon —, donc aucune mémoire n'est nécessaire ici.
        """
        if not from_id or not to_id:
            return
        self._request(
            "PUT",
            f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}",
        )

    def upsert_company(self, company: CRMCompany, *, idempotency_key: str) -> CRMCompany:
        properties = _clean(
            {
                "name": company.name,
                "domain": company.domain,
                "country": company.country_code,
                "industry": company.sector,
                "city": company.city,
                IDEMPOTENCY_PROPERTY: idempotency_key,
                **company.properties,
            }
        )
        company.remote_id = self._write(COMPANIES, idempotency_key, properties)
        return company

    def upsert_contact(self, contact: CRMContact, *, idempotency_key: str) -> CRMContact:
        first, _, last = contact.full_name.partition(" ")
        properties = _clean(
            {
                "email": contact.email,
                "firstname": first or None,
                "lastname": last or None,
                "jobtitle": contact.role,
                "phone": contact.phone,
                IDEMPOTENCY_PROPERTY: idempotency_key,
                **contact.properties,
            }
        )
        contact.remote_id = self._write(CONTACTS, idempotency_key, properties)
        self.associate(
            from_type=CONTACTS,
            from_id=contact.remote_id or "",
            to_type=COMPANIES,
            to_id=contact.company_remote_id or "",
        )
        return contact

    def upsert_deal(self, deal: CRMDeal, *, idempotency_key: str) -> CRMDeal:
        properties = _clean(
            {
                "dealname": deal.name,
                "dealstage": deal.stage,
                "amount": deal.amount,
                IDEMPOTENCY_PROPERTY: idempotency_key,
                **deal.properties,
            }
        )
        deal.remote_id = self._write(DEALS, idempotency_key, properties)
        # Une affaire non associée n'apparaît ni dans la fiche de l'entreprise
        # ni dans celle du contact : invisible là où l'équipe la cherche.
        self.associate(
            from_type=DEALS,
            from_id=deal.remote_id or "",
            to_type=COMPANIES,
            to_id=deal.company_remote_id or "",
        )
        self.associate(
            from_type=DEALS,
            from_id=deal.remote_id or "",
            to_type=CONTACTS,
            to_id=deal.contact_remote_id or "",
        )
        return deal

    def create_task(self, task: CRMTask, *, idempotency_key: str) -> CRMTask:
        properties = _clean(
            {
                "hs_task_subject": task.title,
                "hs_task_body": task.body,
                "hs_timestamp": task.due_at,
                "hs_task_status": "NOT_STARTED",
                IDEMPOTENCY_PROPERTY: idempotency_key,
            }
        )
        task.remote_id = self._write(TASKS, idempotency_key, properties, patch_existing=False)
        self.associate(
            from_type=TASKS,
            from_id=task.remote_id or "",
            to_type=CONTACTS,
            to_id=task.contact_remote_id or "",
        )
        return task

    def create_note(self, note: CRMNote, *, idempotency_key: str) -> CRMNote:
        properties = _clean(
            {
                "hs_note_body": note.body,
                IDEMPOTENCY_PROPERTY: idempotency_key,
            }
        )
        note.remote_id = self._write(NOTES, idempotency_key, properties, patch_existing=False)
        self.associate(
            from_type=NOTES,
            from_id=note.remote_id or "",
            to_type=CONTACTS,
            to_id=note.contact_remote_id or "",
        )
        self.associate(
            from_type=NOTES,
            from_id=note.remote_id or "",
            to_type=COMPANIES,
            to_id=note.company_remote_id or "",
        )
        return note

    def _write(
        self,
        object_type: str,
        idempotency_key: str,
        properties: dict[str, Any],
        *,
        patch_existing: bool = True,
    ) -> str | None:
        """Crée l'objet, ou met à jour celui que cette clé a déjà produit.

        `patch_existing` est faux pour les tâches et les notes : ce sont des
        traces datées d'un moment, pas des fiches qu'on tient à jour. Réécrire
        une note déjà enregistrée effacerait ce qu'elle disait.
        """
        existing = self._resolve_existing(object_type, idempotency_key)
        if existing:
            if patch_existing:
                self._request(
                    "PATCH",
                    f"/crm/v3/objects/{object_type}/{existing}",
                    json={"properties": properties},
                )
            return existing
        data = self._request(
            "POST", f"/crm/v3/objects/{object_type}", json={"properties": properties}
        )
        return self._created(object_type, idempotency_key, data.get("id"))

    def healthcheck(self) -> dict[str, Any]:
        try:
            self._request("GET", "/crm/v3/objects/companies", params={"limit": 1})
        except ConnectorError as exc:
            return {"provider": self.provider, "status": "error", "detail": str(exc)}
        return {"provider": self.provider, "status": "ok"}

    def close(self) -> None:
        self._client.close()


def _clean(properties: dict[str, Any]) -> dict[str, Any]:
    """Retire les valeurs absentes : HubSpot refuse un `null` sur certaines
    propriétés, et envoyer une chaîne vide effacerait la valeur existante."""
    return {key: value for key, value in properties.items() if value is not None}


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
