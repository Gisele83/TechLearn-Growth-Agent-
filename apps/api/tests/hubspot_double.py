"""Doublure de portail HubSpot, pour tester l'adaptateur sans réseau.

Le mock applicatif (`MockCRMAdapter`) est immédiatement cohérent : il ne peut
pas révéler le défaut qui compte ici. HubSpot, lui, indexe ses objets avec du
retard, et c'est précisément dans cette fenêtre qu'une reprise après incident
crée un doublon.

Cette doublure reproduit donc le portail au niveau HTTP :

* un objet créé n'apparaît dans `/search` qu'après `index_delay` créations
  (0 = index immédiat, comme un CRM idéal ; `None` = jamais indexé, le pire
  cas, celui d'une reprise immédiate) ;
* les associations v4 sont enregistrées et consultables ;
* un nombre configurable de réponses 429 et 5xx peut être injecté.

Elle compte aussi les appels : c'est ce qui permet de vérifier la
consommation de quota, pas seulement le résultat final.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from growth_agent.connectors.crm.hubspot import IDEMPOTENCY_PROPERTY


@dataclass
class HubSpotDouble:
    """Portail HubSpot simulé."""

    #: Nombre de créations à attendre avant qu'un objet devienne cherchable.
    #: `None` signifie « jamais » : l'index ne rattrape pas son retard.
    index_delay: int | None = None
    #: Réponses 429 à servir avant la première réponse utile.
    rate_limited_calls: int = 0
    retry_after: str = "1"
    #: Réponses 5xx à servir avant la première réponse utile.
    failing_calls: int = 0

    objects: dict[str, dict[str, dict]] = field(default_factory=dict)
    associations: list[tuple[str, str, str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    _creations: int = 0
    _next_id: int = 1000

    # --- Introspection pour les tests ------------------------------------
    def created(self, object_type: str) -> list[dict]:
        return list(self.objects.get(object_type, {}).values())

    def count(self, object_type: str) -> int:
        return len(self.objects.get(object_type, {}))

    def calls_to(self, fragment: str) -> list[str]:
        return [call for call in self.calls if fragment in call]

    @property
    def searches(self) -> list[str]:
        return self.calls_to("/search")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self, base_url: str = "https://portail.test") -> httpx.Client:
        return httpx.Client(transport=self.transport(), base_url=base_url)

    # --- Acheminement ------------------------------------------------------
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")

        if self.rate_limited_calls > 0:
            self.rate_limited_calls -= 1
            return httpx.Response(429, headers={"Retry-After": self.retry_after}, json={})
        if self.failing_calls > 0:
            self.failing_calls -= 1
            return httpx.Response(503, json={})

        parts = [p for p in path.split("/") if p]
        if "associations" in parts:
            return self._associate(parts)
        if parts[-1] == "search":
            return self._search(parts[-2], json.loads(request.content or b"{}"))
        if request.method == "POST":
            return self._create(parts[-1], json.loads(request.content or b"{}"))
        if request.method == "PATCH":
            return self._patch(parts[-2], parts[-1], json.loads(request.content or b"{}"))
        if request.method == "GET":
            return httpx.Response(200, json={"results": self.created(parts[-1])})
        return httpx.Response(204)

    # --- Opérations --------------------------------------------------------
    def _create(self, object_type: str, payload: dict) -> httpx.Response:
        remote_id = str(self._next_id)
        self._next_id += 1
        self._creations += 1
        self.objects.setdefault(object_type, {})[remote_id] = {
            "id": remote_id,
            "properties": dict(payload.get("properties", {})),
            "indexed_after": self._creations + (self.index_delay or 0),
            "searchable": self.index_delay is not None,
        }
        return httpx.Response(201, json={"id": remote_id})

    def _patch(self, object_type: str, remote_id: str, payload: dict) -> httpx.Response:
        record = self.objects.get(object_type, {}).get(remote_id)
        if record is None:
            return httpx.Response(404, json={})
        record["properties"].update(payload.get("properties", {}))
        return httpx.Response(200, json={"id": remote_id})

    def _search(self, object_type: str, payload: dict) -> httpx.Response:
        wanted = _filter_value(payload, IDEMPOTENCY_PROPERTY)
        results = [
            {"id": record["id"], "properties": record["properties"]}
            for record in self.objects.get(object_type, {}).values()
            if record["searchable"]
            and self._creations >= record["indexed_after"]
            and (wanted is None or record["properties"].get(IDEMPOTENCY_PROPERTY) == wanted)
        ]
        return httpx.Response(200, json={"results": results[:1]})

    def _associate(self, parts: list[str]) -> httpx.Response:
        # /crm/v4/objects/{from_type}/{from_id}/associations/default/{to}/{to_id}
        marker = parts.index("associations")
        from_type, from_id = parts[marker - 2], parts[marker - 1]
        to_type, to_id = parts[-2], parts[-1]
        link = (from_type, from_id, to_type, to_id)
        if link not in self.associations:
            self.associations.append(link)
        return httpx.Response(200, json={})


def _filter_value(payload: dict, property_name: str) -> str | None:
    for group in payload.get("filterGroups", []):
        for condition in group.get("filters", []):
            if condition.get("propertyName") == property_name:
                return condition.get("value")
    return None
