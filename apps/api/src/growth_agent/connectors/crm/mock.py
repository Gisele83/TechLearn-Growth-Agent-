"""CRM en mémoire.

Adaptateur par défaut du MVP : aucun compte, aucun réseau, aucun coût. Il
reproduit volontairement les comportements désagréables d'un vrai CRM —
pagination, quota, idempotence — pour que le code appelant soit éprouvé contre
eux dès le développement.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

from growth_agent.connectors.base import Page, RateLimitError
from growth_agent.connectors.crm.base import (
    CRMAdapter,
    CRMCompany,
    CRMContact,
    CRMDeal,
    CRMNote,
    CRMTask,
)


class MockCRMAdapter(CRMAdapter):
    """CRM factice, déterministe et introspectable par les tests."""

    provider = "mock"

    def __init__(
        self,
        *,
        page_size: int = 50,
        rate_limit_after: int | None = None,
        fail_times: int = 0,
    ) -> None:
        self._companies: dict[str, CRMCompany] = {}
        self._contacts: dict[str, CRMContact] = {}
        self._deals: dict[str, CRMDeal] = {}
        self._tasks: dict[str, CRMTask] = {}
        self._notes: dict[str, CRMNote] = {}
        # Mémoire des clés d'idempotence déjà vues -> identifiant distant produit.
        self._idempotency: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._page_size = page_size
        # Quand `rate_limit_after` est défini, le Nième appel renvoie un 429.
        self._rate_limit_after = rate_limit_after
        self._call_count = 0
        # Nombre d'échecs transitoires à simuler avant de répondre normalement.
        self._fail_times = fail_times
        self.calls: list[str] = []

    # --- Instrumentation -----------------------------------------------------
    def _record(self, operation: str) -> None:
        self._call_count += 1
        self.calls.append(operation)
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RateLimitError("Quota simulé atteint.", provider=self.provider, retry_after=0.01)
        if self._rate_limit_after is not None and self._call_count > self._rate_limit_after:
            raise RateLimitError("Quota simulé atteint.", provider=self.provider, retry_after=0.01)

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids):06d}"

    # --- Lecture -------------------------------------------------------------
    def list_companies(self, *, cursor: str | None = None, limit: int = 50) -> Page:
        self._record("list_companies")
        ordered = sorted(self._companies.values(), key=lambda c: c.remote_id or "")
        start = int(cursor) if cursor else 0
        size = min(limit, self._page_size)
        window = ordered[start : start + size]
        next_cursor = str(start + size) if start + size < len(ordered) else None
        return Page(items=[_company_to_dict(c) for c in window], next_cursor=next_cursor)

    def find_company_by_domain(self, domain: str) -> CRMCompany | None:
        self._record("find_company_by_domain")
        normalized = (domain or "").strip().lower()
        if not normalized:
            return None
        for company in self._companies.values():
            if (company.domain or "").strip().lower() == normalized:
                return company
        return None

    def find_contact_by_email(self, email: str) -> CRMContact | None:
        self._record("find_contact_by_email")
        normalized = (email or "").strip().lower()
        for contact in self._contacts.values():
            if contact.email.strip().lower() == normalized:
                return contact
        return None

    # --- Écriture ------------------------------------------------------------
    def _replay(self, idempotency_key: str) -> str | None:
        return self._idempotency.get(idempotency_key)

    def upsert_company(self, company: CRMCompany, *, idempotency_key: str) -> CRMCompany:
        self._record("upsert_company")
        existing_id = self._replay(idempotency_key)
        if existing_id:
            return self._companies[existing_id]
        if company.domain:
            for stored in self._companies.values():
                if (stored.domain or "").lower() == company.domain.lower():
                    stored.name = company.name or stored.name
                    stored.properties.update(company.properties)
                    self._idempotency[idempotency_key] = stored.remote_id or ""
                    return stored
        remote_id = company.remote_id or self._next_id("comp")
        company.remote_id = remote_id
        self._companies[remote_id] = company
        self._idempotency[idempotency_key] = remote_id
        return company

    def upsert_contact(self, contact: CRMContact, *, idempotency_key: str) -> CRMContact:
        self._record("upsert_contact")
        existing_id = self._replay(idempotency_key)
        if existing_id:
            return self._contacts[existing_id]
        for stored in self._contacts.values():
            if stored.email.lower() == contact.email.lower():
                stored.full_name = contact.full_name or stored.full_name
                stored.role = contact.role or stored.role
                stored.properties.update(contact.properties)
                self._idempotency[idempotency_key] = stored.remote_id or ""
                return stored
        remote_id = contact.remote_id or self._next_id("cont")
        contact.remote_id = remote_id
        self._contacts[remote_id] = contact
        self._idempotency[idempotency_key] = remote_id
        return contact

    def upsert_deal(self, deal: CRMDeal, *, idempotency_key: str) -> CRMDeal:
        self._record("upsert_deal")
        existing_id = self._replay(idempotency_key)
        if existing_id:
            return self._deals[existing_id]
        remote_id = deal.remote_id or self._next_id("deal")
        deal.remote_id = remote_id
        self._deals[remote_id] = deal
        self._idempotency[idempotency_key] = remote_id
        return deal

    def create_task(self, task: CRMTask, *, idempotency_key: str) -> CRMTask:
        self._record("create_task")
        existing_id = self._replay(idempotency_key)
        if existing_id:
            return self._tasks[existing_id]
        remote_id = self._next_id("task")
        task.remote_id = remote_id
        self._tasks[remote_id] = task
        self._idempotency[idempotency_key] = remote_id
        return task

    def create_note(self, note: CRMNote, *, idempotency_key: str) -> CRMNote:
        self._record("create_note")
        existing_id = self._replay(idempotency_key)
        if existing_id:
            return self._notes[existing_id]
        remote_id = self._next_id("note")
        note.remote_id = remote_id
        self._notes[remote_id] = note
        self._idempotency[idempotency_key] = remote_id
        return note

    # --- Supervision ---------------------------------------------------------
    def healthcheck(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": "ok",
            "mode": "in-memory",
            "companies": len(self._companies),
            "contacts": len(self._contacts),
            "deals": len(self._deals),
            "checked_at": time.time(),
        }

    # --- Aides de test -------------------------------------------------------
    def seed_company(self, **kwargs: Any) -> CRMCompany:
        company = CRMCompany(**kwargs)
        company.remote_id = company.remote_id or self._next_id("comp")
        self._companies[company.remote_id] = company
        return company

    @property
    def company_count(self) -> int:
        return len(self._companies)

    @property
    def contact_count(self) -> int:
        return len(self._contacts)

    @property
    def task_count(self) -> int:
        return len(self._tasks)


def _company_to_dict(company: CRMCompany) -> dict[str, Any]:
    return {
        "remote_id": company.remote_id,
        "name": company.name,
        "domain": company.domain,
        "country_code": company.country_code,
        "sector": company.sector,
        "city": company.city,
        "properties": dict(company.properties),
    }
