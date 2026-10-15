"""Fournisseur de recherche HTTP générique (Apollo, Clay, annuaire public…).

Volontairement neutre : la réponse est projetée via une table de correspondance
de champs configurable, pour qu'un changement de fournisseur se règle par la
configuration plutôt que par du code.

Garde-fou de licéité : tout résultat sans URL exploitable est écarté, et aucun
email n'est déduit — seul un email fourni par la source est conservé.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from growth_agent.connectors.base import CircuitBreaker, ConnectorError, RateLimitError
from growth_agent.connectors.search.base import SearchProvider, SearchQuery, SearchResult

DEFAULT_FIELD_MAP: dict[str, str] = {
    "organization_name": "name",
    "url": "url",
    "domain": "domain",
    "country_code": "country",
    "city": "city",
    "sector": "industry",
    "headcount_estimate": "employee_count",
    "title": "title",
    "excerpt": "snippet",
    "contact_full_name": "contact_name",
    "contact_role": "contact_title",
    "contact_email": "contact_email",
}


class HTTPSearchProvider(SearchProvider):
    provider = "http"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        search_path: str = "/search",
        field_map: dict[str, str] | None = None,
        license_note: str = "",
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ConnectorError("URL de recherche absente.", provider=self.provider)
        self._field_map = {**DEFAULT_FIELD_MAP, **(field_map or {})}
        self._search_path = search_path
        self._license_note = license_note or (
            "Vérifier les conditions d'utilisation du fournisseur avant exploitation."
        )
        self._breaker = CircuitBreaker()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )

    def search(self, query: SearchQuery) -> list[SearchResult]:
        payload = {
            "countries": query.country_codes,
            "sectors": query.sectors,
            "keywords": query.keywords,
            "limit": query.limit,
        }
        response = self._breaker.call(
            lambda: self._client.post(self._search_path, json=payload), provider=self.provider
        )
        if response.status_code == 429:
            raise RateLimitError(
                "Quota du fournisseur de recherche atteint.", provider=self.provider
            )
        if response.status_code >= 400:
            raise ConnectorError(
                f"Recherche refusée ({response.status_code}).", provider=self.provider
            )

        now = datetime.now(UTC)
        results: list[SearchResult] = []
        for raw in response.json().get("results", []):
            mapped = self._map(raw)
            if not mapped.get("url") or not mapped.get("organization_name"):
                # Sans provenance démontrable, le résultat est inutilisable.
                continue
            results.append(
                SearchResult(
                    organization_name=mapped["organization_name"],
                    url=mapped["url"],
                    collected_at=now,
                    source_kind=mapped.get("source_kind") or "directory",
                    title=mapped.get("title") or "",
                    excerpt=mapped.get("excerpt") or "",
                    domain=mapped.get("domain"),
                    country_code=(mapped.get("country_code") or "FR")[:2].upper(),
                    city=mapped.get("city"),
                    sector=mapped.get("sector"),
                    headcount_estimate=_as_int(mapped.get("headcount_estimate")),
                    contact_full_name=mapped.get("contact_full_name"),
                    contact_role=mapped.get("contact_role"),
                    contact_email=mapped.get("contact_email"),
                    provider=self.provider,
                    license_note=self._license_note,
                    raw={},
                )
            )
        return results

    def _map(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {local: raw.get(remote) for local, remote in self._field_map.items()}

    def close(self) -> None:
        self._client.close()


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
