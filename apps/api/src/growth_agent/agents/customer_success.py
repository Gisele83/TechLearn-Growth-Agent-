"""Customer Success — suivi des pilotes et des renouvellements.

Garde-fou : accès limité aux statuts autorisés.

L'agent lit Maintrix et EduCore uniquement à travers un `ProductAdapter`, qui
n'expose que quatre types d'événements et une poignée de champs. Les charges
utiles sont filtrées à la frontière du connecteur : même si une plateforme
envoyait un jour des données d'élève ou d'équipement, elles ne seraient pas
stockées. Aucune écriture n'est possible — l'interface n'en propose aucune.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.connectors.product.base import ProductAdapter
from growth_agent.models.catalog import Product
from growth_agent.models.enums import AgentName, OutreachEventType, ProspectStage
from growth_agent.models.ops import ProductPilotEvent
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.services.audit import record_audit
from growth_agent.services.outreach import record_outreach_event

RENEWAL_ALERT_DAYS = 60


@dataclass(slots=True)
class PilotSyncOutcome:
    imported: int = 0
    duplicates: int = 0
    renewals_due: list[dict[str, Any]] = field(default_factory=list)
    read_only: bool = True


class CustomerSuccessAgent(BaseAgent[PilotSyncOutcome]):
    name = AgentName.CUSTOMER_SUCCESS
    guardrail = "Lecture seule, limitée aux statuts commerciaux explicitement autorisés."

    def __init__(self, adapter: ProductAdapter) -> None:
        self._adapter = adapter

    def run(  # type: ignore[override]
        self,
        context: AgentContext,
        *,
        product: Product,
        since: datetime | None = None,
        limit: int = 100,
        **_: Any,
    ) -> AgentResult[PilotSyncOutcome]:
        outcome = PilotSyncOutcome(read_only=self._adapter.is_read_only)
        warnings: list[str] = []

        if self._adapter.mode == "mock":
            warnings.append(
                f"Connecteur {product.code} en mode simulé : les données de pilote sont fictives."
            )

        events = self._adapter.list_events(since=since, limit=limit)
        for event in events:
            existing = context.session.execute(
                select(ProductPilotEvent).where(
                    ProductPilotEvent.product_id == product.id,
                    ProductPilotEvent.external_event_id == event.event_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                outcome.duplicates += 1
                continue

            prospect = _match_prospect(context, product, event.external_org_ref)
            context.session.add(
                ProductPilotEvent(
                    product_id=product.id,
                    prospect_id=prospect.id if prospect else None,
                    event_type=event.event_type,
                    external_event_id=event.event_id,
                    external_org_ref=event.external_org_ref,
                    occurred_at=event.occurred_at,
                    received_at=context.moment,
                    status=event.status,
                    payload=dict(event.payload),
                    source_adapter=self._adapter.mode,
                )
            )
            outcome.imported += 1

            if prospect is not None and event.event_type == "pilot.activated":
                prospect.stage = ProspectStage.PILOT
                record_outreach_event(
                    context.session,
                    product_id=product.id,
                    campaign_id=prospect.campaign_id,
                    prospect_id=prospect.id,
                    event_type=OutreachEventType.PILOT_STARTED,
                    occurred_at=event.occurred_at,
                    actor=self.name,
                    idempotency_key=f"pilot:{product.code}:{event.event_id}",
                )

            if event.event_type == "license.status_changed":
                snapshot = self._adapter.get_pilot_snapshot(event.external_org_ref)
                if snapshot and snapshot.renewal_date:
                    days_left = (snapshot.renewal_date - context.moment).days
                    if 0 <= days_left <= RENEWAL_ALERT_DAYS:
                        outcome.renewals_due.append(
                            {
                                "external_org_ref": event.external_org_ref,
                                "renewal_date": snapshot.renewal_date.isoformat(),
                                "days_left": days_left,
                                "license_status": str(snapshot.license_status),
                            }
                        )

        context.session.flush()
        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="customer_success.pilot_sync",
            object_type="product",
            object_id=product.id,
            product_code=product.code,
            metadata={
                "adapter_mode": self._adapter.mode,
                "imported": outcome.imported,
                "duplicates": outcome.duplicates,
                "renewals_due": len(outcome.renewals_due),
            },
            now=context.moment,
        )
        return AgentResult(
            value=outcome,
            summary={
                "imported": outcome.imported,
                "duplicates": outcome.duplicates,
                "renewals_due": len(outcome.renewals_due),
            },
            warnings=warnings,
        )


def _match_prospect(
    context: AgentContext, product: Product, external_org_ref: str
) -> ProspectCandidate | None:
    """Rapproche une référence externe d'un prospect connu.

    Le rapprochement est volontairement conservateur : si la référence n'a pas
    été enregistrée dans les notes du prospect, on n'essaie pas de deviner par
    ressemblance de nom. Un rapprochement erroné fausserait le funnel.
    """
    if not external_org_ref:
        return None
    return (
        context.session.execute(
            select(ProspectCandidate).where(
                ProspectCandidate.product_id == product.id,
                ProspectCandidate.notes.contains(external_org_ref),
            )
        )
        .scalars()
        .first()
    )
