"""Interface de recherche de prospects.

Contrainte de licéité : seules des sources publiques et dont les conditions
d'utilisation autorisent la consultation automatisée peuvent être exploitées.
Chaque résultat porte obligatoirement une URL et une date de collecte — sans
quoi la provenance n'est pas démontrable et le prospect reste `unknown`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class SearchResult:
    """Organisation repérée, avec sa preuve de provenance."""

    organization_name: str
    url: str
    collected_at: datetime
    source_kind: str
    title: str = ""
    excerpt: str = ""
    domain: str | None = None
    country_code: str = "FR"
    city: str | None = None
    sector: str | None = None
    headcount_estimate: int | None = None
    contact_full_name: str | None = None
    contact_role: str | None = None
    contact_email: str | None = None
    provider: str = "mock"
    license_note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("Un résultat de recherche sans URL n'est pas exploitable.")
        if not self.organization_name:
            raise ValueError("Un résultat de recherche sans nom d'organisation est rejeté.")


@dataclass(slots=True)
class SearchQuery:
    product_code: str
    country_codes: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    limit: int = 25


class SearchProvider(ABC):
    provider: str = "abstract"

    @abstractmethod
    def search(self, query: SearchQuery) -> list[SearchResult]:
        """Renvoie des organisations, jamais de contacts inventés.

        Une implémentation ne doit JAMAIS deviner une adresse email à partir
        d'un nom et d'un domaine : un contact sans source est un contact absent.
        """

    def healthcheck(self) -> dict[str, Any]:
        return {"provider": self.provider, "status": "ok"}
