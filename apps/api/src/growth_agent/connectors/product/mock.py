"""Adaptateurs produits factices.

Jeu de données inventé permettant de dérouler le cycle pilote → licence sans
toucher à Maintrix ni à EduCore. C'est le mode par défaut et, aujourd'hui, le
seul mode réellement utilisable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from growth_agent.connectors.product.base import (
    LicenseStatus,
    PilotSnapshot,
    PilotStatus,
    ProductAdapter,
    ProductEvent,
)


class _BaseMockProductAdapter(ProductAdapter):
    mode = "mock"

    def __init__(self, *, now: datetime | None = None) -> None:
        self._now = now or datetime.now(UTC)
        self._events: list[ProductEvent] = []
        self._snapshots: dict[str, PilotSnapshot] = {}
        self._seed()

    def _seed(self) -> None:  # pragma: no cover - surchargé
        raise NotImplementedError

    def _add_event(
        self,
        *,
        event_id: str,
        event_type: str,
        org_ref: str,
        days_ago: int,
        status: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            ProductEvent(
                event_id=event_id,
                event_type=event_type,
                external_org_ref=org_ref,
                occurred_at=self._now - timedelta(days=days_ago),
                status=status,
                payload=payload or {},
            )
        )

    def list_events(self, *, since: datetime | None = None, limit: int = 100) -> list[ProductEvent]:
        events = sorted(self._events, key=lambda e: e.occurred_at)
        if since is not None:
            events = [e for e in events if e.occurred_at > since]
        return events[:limit]

    def get_pilot_snapshot(self, external_org_ref: str) -> PilotSnapshot | None:
        return self._snapshots.get(external_org_ref)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "product": self.product_code,
            "mode": self.mode,
            "status": "ok",
            "read_only": self.is_read_only,
            "events": len(self._events),
            "note": "Données fictives — aucune connexion à la plateforme réelle.",
        }


class MockMaintrixAdapter(_BaseMockProductAdapter):
    product_code = "maintrix"

    def _seed(self) -> None:
        self._add_event(
            event_id="mtx-evt-001",
            event_type="pilot.invited",
            org_ref="mtx-org-kribi",
            days_ago=40,
            status="invited",
        )
        self._add_event(
            event_id="mtx-evt-002",
            event_type="pilot.activated",
            org_ref="mtx-org-kribi",
            days_ago=31,
            status="active",
        )
        self._add_event(
            event_id="mtx-evt-003",
            event_type="pilot.feedback_received",
            org_ref="mtx-org-kribi",
            days_ago=12,
            status="active",
            payload={
                "shareable": True,
                "comment": "Le suivi des arrêts machine est plus lisible qu'avec le tableur.",
            },
        )
        self._add_event(
            event_id="mtx-evt-004",
            event_type="license.status_changed",
            org_ref="mtx-org-kribi",
            days_ago=5,
            status="trial",
        )
        self._add_event(
            event_id="mtx-evt-005",
            event_type="pilot.invited",
            org_ref="mtx-org-agro-douala",
            days_ago=9,
            status="invited",
        )
        self._snapshots = {
            "mtx-org-kribi": PilotSnapshot(
                external_org_ref="mtx-org-kribi",
                pilot_status=PilotStatus.ACTIVE,
                license_status=LicenseStatus.TRIAL,
                renewal_date=self._now + timedelta(days=55),
                last_activity_at=self._now - timedelta(days=2),
                shareable_feedback=[
                    "Le suivi des arrêts machine est plus lisible qu'avec le tableur."
                ],
            ),
            "mtx-org-agro-douala": PilotSnapshot(
                external_org_ref="mtx-org-agro-douala",
                pilot_status=PilotStatus.INVITED,
                license_status=LicenseStatus.NONE,
            ),
        }


class MockEduCoreAdapter(_BaseMockProductAdapter):
    product_code = "educore"

    def _seed(self) -> None:
        self._add_event(
            event_id="edu-evt-001",
            event_type="pilot.invited",
            org_ref="edu-org-palmiers",
            days_ago=35,
            status="invited",
        )
        self._add_event(
            event_id="edu-evt-002",
            event_type="pilot.activated",
            org_ref="edu-org-palmiers",
            days_ago=28,
            status="active",
        )
        self._add_event(
            event_id="edu-evt-003",
            event_type="pilot.feedback_received",
            org_ref="edu-org-palmiers",
            days_ago=7,
            status="active",
            payload={
                "shareable": True,
                "comment": (
                    "Les inscriptions de rentrée ont pris deux jours au lieu de deux semaines."
                ),
            },
        )
        self._add_event(
            event_id="edu-evt-004",
            event_type="license.status_changed",
            org_ref="edu-org-palmiers",
            days_ago=3,
            status="active",
        )
        self._add_event(
            event_id="edu-evt-005",
            event_type="pilot.invited",
            org_ref="edu-org-nkolbisson",
            days_ago=6,
            status="invited",
        )
        self._snapshots = {
            "edu-org-palmiers": PilotSnapshot(
                external_org_ref="edu-org-palmiers",
                pilot_status=PilotStatus.ACTIVE,
                license_status=LicenseStatus.ACTIVE,
                renewal_date=self._now + timedelta(days=300),
                last_activity_at=self._now - timedelta(days=1),
                shareable_feedback=[
                    "Les inscriptions de rentrée ont pris deux jours au lieu de deux semaines."
                ],
            ),
            "edu-org-nkolbisson": PilotSnapshot(
                external_org_ref="edu-org-nkolbisson",
                pilot_status=PilotStatus.INVITED,
                license_status=LicenseStatus.NONE,
            ),
        }
