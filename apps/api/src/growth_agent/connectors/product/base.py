"""Interface de lecture vers Maintrix et EduCore.

PÉRIMÈTRE STRICTEMENT LIMITÉ — à lire avant toute évolution de ce fichier.

Growth Agent ne lit que les statuts commerciaux strictement nécessaires au
suivi des pilotes et des licences :
  * invitation à un pilote,
  * activation d'un pilote,
  * statut et date de renouvellement d'une licence,
  * retours de pilote explicitement marqués comme partageables.

Ce que cette interface n'expose pas, et ne doit jamais exposer :
  * aucune donnée d'élève, de parent ou de personnel (EduCore) ;
  * aucune donnée d'exploitation, d'équipement ou d'incident (Maintrix) ;
  * aucune écriture, d'aucune sorte, dans l'une ou l'autre plateforme.

Les endpoints décrits ici N'EXISTENT PAS encore. Les contrats proposés sont
dans `packages/contracts/openapi/product-adapter.openapi.yaml` et la demande
d'autorisation correspondante dans `docs/11-dependances-autorisation.md`.
Tant qu'ils ne sont pas créés et autorisés côté plateformes, seuls les
adaptateurs mock sont utilisables.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class PilotStatus(StrEnum):
    NOT_INVITED = "not_invited"
    INVITED = "invited"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class LicenseStatus(StrEnum):
    NONE = "none"
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


@dataclass(slots=True)
class ProductEvent:
    """Événement commercial lu depuis une plateforme produit.

    `external_org_ref` est une référence opaque fournie par la plateforme. Ce
    n'est pas un identifiant de base : Growth Agent n'a pas à connaître la
    structure interne de Maintrix ou EduCore.
    """

    event_id: str
    event_type: str
    external_org_ref: str
    occurred_at: datetime
    status: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PilotSnapshot:
    external_org_ref: str
    pilot_status: PilotStatus
    license_status: LicenseStatus
    renewal_date: datetime | None = None
    last_activity_at: datetime | None = None
    shareable_feedback: list[str] = field(default_factory=list)


class ProductAdapter(ABC):
    """Lecture seule. Aucune méthode d'écriture n'est définie, par construction."""

    product_code: str = "abstract"
    mode: str = "mock"

    @abstractmethod
    def list_events(self, *, since: datetime | None = None, limit: int = 100) -> list[ProductEvent]:
        """Événements survenus depuis `since`, du plus ancien au plus récent."""

    @abstractmethod
    def get_pilot_snapshot(self, external_org_ref: str) -> PilotSnapshot | None:
        """État courant d'une organisation, ou None si elle est inconnue."""

    @abstractmethod
    def healthcheck(self) -> dict[str, Any]: ...

    @property
    def is_read_only(self) -> bool:
        """Toujours vrai. Présent pour être affirmé explicitement dans les tests."""
        return True
