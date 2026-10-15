"""Adaptateur HTTP vers une plateforme produit — DÉSACTIVÉ PAR DÉFAUT.

Ce code est prêt mais inerte. Il ne s'initialise que si la configuration
l'autorise explicitement (`GA_<PRODUIT>_ENABLED=true`, `*_ADAPTER=http`, URL de
base renseignée). À défaut, `ConnectorDisabledError` est levée AVANT toute
tentative de connexion : aucun paquet ne part vers Maintrix ou EduCore.

Il n'émet que des requêtes GET, sur quatre chemins en lecture. Aucune méthode
d'écriture n'est implémentée, et aucune ne doit l'être sans autorisation écrite
des responsables de la plateforme concernée.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from growth_agent.connectors.base import (
    CircuitBreaker,
    ConnectorDisabledError,
    ConnectorError,
    RateLimitError,
)
from growth_agent.connectors.product.base import (
    LicenseStatus,
    PilotSnapshot,
    PilotStatus,
    ProductAdapter,
    ProductEvent,
)

ALLOWED_EVENT_TYPES = frozenset(
    {
        "pilot.invited",
        "pilot.activated",
        "pilot.feedback_received",
        "license.status_changed",
    }
)


class HTTPProductAdapter(ProductAdapter):
    mode = "http"

    def __init__(
        self,
        *,
        product_code: str,
        base_url: str,
        api_token: str,
        enabled: bool,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not enabled:
            raise ConnectorDisabledError(
                f"Connecteur {product_code} désactivé : aucune requête ne sera émise. "
                "Activation soumise à autorisation (voir docs/11-dependances-autorisation.md).",
                provider=product_code,
            )
        if not base_url:
            raise ConnectorDisabledError(
                f"URL de base absente pour {product_code} : connecteur inerte.",
                provider=product_code,
            )
        self.product_code = product_code
        self._breaker = CircuitBreaker()
        headers = {"Accept": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        response = self._breaker.call(
            lambda: self._client.get(path, params={k: v for k, v in params.items() if v is not None}),
            provider=self.product_code,
        )
        if response.status_code == 429:
            raise RateLimitError(
                f"Quota atteint sur {self.product_code}.", provider=self.product_code
            )
        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise ConnectorError(
                f"Lecture {self.product_code} refusée ({response.status_code}).",
                provider=self.product_code,
            )
        return response.json()

    def list_events(self, *, since: datetime | None = None, limit: int = 100) -> list[ProductEvent]:
        data = self._get(
            "/growth/v1/events",
            since=since.isoformat() if since else None,
            limit=min(limit, 500),
        )
        events: list[ProductEvent] = []
        for raw in data.get("events", []):
            event_type = raw.get("type", "")
            # Filtre de périmètre : tout type non prévu au contrat est ignoré,
            # même si la plateforme se met à en émettre d'autres.
            if event_type not in ALLOWED_EVENT_TYPES:
                continue
            occurred_at = _parse_datetime(raw.get("occurred_at"))
            if occurred_at is None:
                continue
            events.append(
                ProductEvent(
                    event_id=str(raw.get("id", "")),
                    event_type=event_type,
                    external_org_ref=str(raw.get("organization_ref", "")),
                    occurred_at=occurred_at,
                    status=str(raw.get("status", "")),
                    payload=_filter_payload(raw.get("data", {})),
                )
            )
        return sorted(events, key=lambda e: e.occurred_at)

    def get_pilot_snapshot(self, external_org_ref: str) -> PilotSnapshot | None:
        data = self._get(f"/growth/v1/organizations/{external_org_ref}/status")
        if not data:
            return None
        return PilotSnapshot(
            external_org_ref=external_org_ref,
            pilot_status=_as_enum(PilotStatus, data.get("pilot_status"), PilotStatus.NOT_INVITED),
            license_status=_as_enum(
                LicenseStatus, data.get("license_status"), LicenseStatus.NONE
            ),
            renewal_date=_parse_datetime(data.get("renewal_date")),
            last_activity_at=_parse_datetime(data.get("last_activity_at")),
            shareable_feedback=[
                str(item) for item in data.get("shareable_feedback", []) if item
            ][:20],
        )

    def healthcheck(self) -> dict[str, Any]:
        try:
            self._get("/growth/v1/health")
        except ConnectorError as exc:
            return {"product": self.product_code, "mode": self.mode, "status": "error", "detail": str(exc)}
        return {"product": self.product_code, "mode": self.mode, "status": "ok", "read_only": True}

    def close(self) -> None:
        self._client.close()


# Champs acceptés dans la charge utile d'un événement. Tout le reste est écarté
# à la frontière : si la plateforme envoie un jour des données d'élève ou
# d'équipement, elles ne seront jamais stockées par Growth Agent.
ALLOWED_PAYLOAD_KEYS = frozenset(
    {"shareable", "comment", "plan", "seats", "renewal_date", "reason", "locale"}
)


def _filter_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k in ALLOWED_PAYLOAD_KEYS}


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_enum(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(str(value))
    except (ValueError, TypeError):
        return default
