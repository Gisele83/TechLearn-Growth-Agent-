"""Interface CRM.

Le CRM est la source de vérité des entreprises, contacts et opportunités.
Growth Agent n'en recopie rien : il écrit via cette interface et conserve
seulement la correspondance d'identifiants.

Toutes les écritures acceptent une clé d'idempotence. Deux appels portant la
même clé doivent produire un seul objet distant — c'est ce qui rend une reprise
après incident sûre.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from growth_agent.connectors.base import Page


class CRMIdempotencyStore(Protocol):
    """Mémoire locale des objets déjà créés dans le CRM.

    Elle existe parce qu'on ne peut pas confier l'idempotence au CRM lui-même.
    HubSpot n'offre pas d'en-tête d'idempotence, et son index de recherche est
    à cohérence différée : un objet créé n'y apparaît qu'après un délai. Une
    reprise dans cette fenêtre ne trouve rien et crée un doublon — exactement
    ce que le mécanisme devait empêcher.

    Growth Agent tient donc la correspondance lui-même, écrite dans la même
    transaction que le reste du travail. La recherche distante ne sert plus que
    de filet, quand la mémoire locale est vide.
    """

    def lookup(self, *, provider: str, object_type: str, idempotency_key: str) -> str | None:
        """Identifiant distant déjà associé à cette clé, s'il est connu."""
        ...

    def remember(
        self,
        *,
        provider: str,
        object_type: str,
        idempotency_key: str,
        remote_id: str,
    ) -> None:
        """Associe durablement la clé à l'identifiant rendu par le CRM."""
        ...


class NullIdempotencyStore:
    """Absence de mémoire : chaque appel repart de la recherche distante.

    C'est le comportement par défaut, et il est volontairement explicite. Un
    adaptateur sans mémoire locale est exposé au doublon décrit ci-dessus ;
    mieux vaut que ce soit un choix lisible qu'un oubli.
    """

    def lookup(self, *, provider: str, object_type: str, idempotency_key: str) -> str | None:
        return None

    def remember(
        self, *, provider: str, object_type: str, idempotency_key: str, remote_id: str
    ) -> None:
        return None


class InMemoryIdempotencyStore:
    """Mémoire de processus. Utile aux tests et au mock ; perdue au
    redémarrage, donc jamais suffisante en exploitation."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], str] = {}

    def lookup(self, *, provider: str, object_type: str, idempotency_key: str) -> str | None:
        return self._entries.get((provider, object_type, idempotency_key))

    def remember(
        self, *, provider: str, object_type: str, idempotency_key: str, remote_id: str
    ) -> None:
        self._entries.setdefault((provider, object_type, idempotency_key), remote_id)


@dataclass(slots=True)
class CRMCompany:
    name: str
    domain: str | None = None
    country_code: str | None = None
    sector: str | None = None
    city: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    remote_id: str | None = None


@dataclass(slots=True)
class CRMContact:
    email: str
    full_name: str = ""
    role: str | None = None
    phone: str | None = None
    company_remote_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    remote_id: str | None = None


@dataclass(slots=True)
class CRMDeal:
    name: str
    stage: str
    amount: float | None = None
    currency: str = "EUR"
    company_remote_id: str | None = None
    contact_remote_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    remote_id: str | None = None


@dataclass(slots=True)
class CRMTask:
    title: str
    due_at: str | None = None
    owner_email: str | None = None
    contact_remote_id: str | None = None
    body: str = ""
    remote_id: str | None = None


@dataclass(slots=True)
class CRMNote:
    body: str
    contact_remote_id: str | None = None
    company_remote_id: str | None = None
    remote_id: str | None = None


class CRMAdapter(ABC):
    """Contrat minimal attendu d'un CRM."""

    provider: str = "abstract"

    # --- Lecture -------------------------------------------------------------
    @abstractmethod
    def list_companies(self, *, cursor: str | None = None, limit: int = 50) -> Page:
        """Page d'entreprises. `limit` est un plafond, pas une garantie."""

    @abstractmethod
    def find_company_by_domain(self, domain: str) -> CRMCompany | None:
        """Recherche exacte par domaine, utilisée pour le dédoublonnage."""

    @abstractmethod
    def find_contact_by_email(self, email: str) -> CRMContact | None: ...

    # --- Écriture ------------------------------------------------------------
    @abstractmethod
    def upsert_company(self, company: CRMCompany, *, idempotency_key: str) -> CRMCompany: ...

    @abstractmethod
    def upsert_contact(self, contact: CRMContact, *, idempotency_key: str) -> CRMContact: ...

    @abstractmethod
    def upsert_deal(self, deal: CRMDeal, *, idempotency_key: str) -> CRMDeal: ...

    @abstractmethod
    def create_task(self, task: CRMTask, *, idempotency_key: str) -> CRMTask: ...

    @abstractmethod
    def create_note(self, note: CRMNote, *, idempotency_key: str) -> CRMNote: ...

    # --- Mémoire d'idempotence ----------------------------------------------
    def with_idempotency_store(self, store: CRMIdempotencyStore) -> CRMAdapter:
        """Adaptateur équivalent, adossé à cette mémoire.

        Renvoie une vue plutôt que de muter l'adaptateur : celui-ci est partagé
        entre requêtes, alors que la mémoire est adossée à une transaction. Un
        attribut mutable partagé ferait écrire une requête dans la transaction
        d'une autre.
        """
        return self

    # --- Supervision ---------------------------------------------------------
    @abstractmethod
    def healthcheck(self) -> dict[str, Any]:
        """État du connecteur, sans jamais révéler d'identifiant."""

    def iter_companies(self, *, page_size: int = 50, max_pages: int = 200):
        """Parcourt toutes les pages. `max_pages` évite une boucle infinie
        si un fournisseur renvoie indéfiniment le même curseur."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(max_pages):
            page = self.list_companies(cursor=cursor, limit=page_size)
            yield from page.items
            if not page.has_more:
                return
            assert page.next_cursor is not None
            if page.next_cursor in seen_cursors:
                return
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
