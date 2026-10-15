"""Indicateurs recalculés à partir des événements.

Aucun compteur n'est stocké : chaque KPI est une agrégation des tables
`outreach_events`, `prospect_candidates` et `product_pilot_events`. Deux appels
successifs sur la même fenêtre donnent le même résultat, et tout chiffre
affiché peut être remonté jusqu'aux événements qui le composent.

Les taux ne sont calculés que si le dénominateur est non nul ; sinon ils valent
`None` et l'interface affiche « donnée insuffisante » plutôt que 0 %.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.models.catalog import Product
from growth_agent.models.enums import OutreachEventType, ProspectStage, VerificationStatus
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.prospect import ProspectCandidate

DEFAULT_WINDOW_DAYS = 90


@dataclass(slots=True)
class FunnelCounts:
    discovered: int = 0
    qualified: int = 0
    contacted: int = 0
    replied: int = 0
    demos_scheduled: int = 0
    demos_held: int = 0
    pilots_started: int = 0
    won: int = 0
    lost: int = 0
    opted_out: int = 0
    blocked: int = 0


@dataclass(slots=True)
class ProductKPIs:
    product_code: str
    window_days: int
    computed_at: datetime
    window_start: datetime
    funnel: FunnelCounts
    reply_rate: float | None = None
    demo_rate: float | None = None
    pilot_rate: float | None = None
    qualified_organizations: int = 0
    verified_organizations: int = 0
    unknown_organizations: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["computed_at"] = self.computed_at.isoformat()
        payload["window_start"] = self.window_start.isoformat()
        return payload


def _ratio(numerator: int, denominator: int) -> float | None:
    """`None` plutôt que 0 quand il n'y a rien à diviser : un taux sans base
    n'est pas une performance de 0 %, c'est une absence de donnée."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def compute_product_kpis(
    session: Session,
    *,
    product_id: uuid.UUID,
    product_code: str = "",
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> ProductKPIs:
    moment = now or datetime.now(UTC)
    window_start = moment - timedelta(days=window_days)

    # On compte des ORGANISATIONS distinctes, pas des événements : un prospect
    # qui répond trois fois à un même message a répondu une fois. Sans cela, un
    # « taux de réponse » pourrait dépasser 100 %, ce qui ne veut rien dire.
    rows = session.execute(
        select(
            OutreachEvent.event_type,
            func.count(func.distinct(OutreachEvent.prospect_id)),
        )
        .where(
            OutreachEvent.product_id == product_id,
            OutreachEvent.occurred_at >= window_start,
            OutreachEvent.occurred_at <= moment,
            OutreachEvent.prospect_id.is_not(None),
        )
        .group_by(OutreachEvent.event_type)
    ).all()
    counts = {event_type: int(total) for event_type, total in rows}

    # Les blocages, eux, se comptent en occurrences : trois tentatives bloquées
    # sur le même prospect sont trois incidents à examiner.
    blocked_total = int(
        session.execute(
            select(func.count())
            .select_from(OutreachEvent)
            .where(
                OutreachEvent.product_id == product_id,
                OutreachEvent.event_type == OutreachEventType.SEND_BLOCKED,
                OutreachEvent.occurred_at >= window_start,
                OutreachEvent.occurred_at <= moment,
            )
        ).scalar_one()
    )

    stage_rows = session.execute(
        select(ProspectCandidate.stage, func.count())
        .where(ProspectCandidate.product_id == product_id)
        .group_by(ProspectCandidate.stage)
    ).all()
    stages = {stage: int(total) for stage, total in stage_rows}

    verif_rows = session.execute(
        select(ProspectCandidate.verification_status, func.count())
        .where(ProspectCandidate.product_id == product_id)
        .group_by(ProspectCandidate.verification_status)
    ).all()
    verifications = {status: int(total) for status, total in verif_rows}

    funnel = FunnelCounts(
        discovered=sum(stages.values()),
        qualified=sum(
            total
            for stage, total in stages.items()
            if stage not in {ProspectStage.DISCOVERED, ProspectStage.DISQUALIFIED}
        ),
        contacted=counts.get(OutreachEventType.SENT, 0),
        replied=counts.get(OutreachEventType.REPLIED, 0),
        demos_scheduled=counts.get(OutreachEventType.DEMO_SCHEDULED, 0),
        demos_held=counts.get(OutreachEventType.DEMO_HELD, 0),
        pilots_started=counts.get(OutreachEventType.PILOT_STARTED, 0),
        won=counts.get(OutreachEventType.DEAL_WON, 0),
        lost=counts.get(OutreachEventType.DEAL_LOST, 0),
        opted_out=counts.get(OutreachEventType.OPTED_OUT, 0),
        blocked=blocked_total,
    )

    notes: list[str] = []
    if funnel.contacted == 0:
        notes.append(
            "Aucun envoi sur la période : les taux de réponse et de conversion "
            "ne sont pas calculables."
        )
    if funnel.blocked:
        notes.append(
            f"{funnel.blocked} envoi(s) bloqué(s) par un garde-fou — voir le journal d'audit."
        )

    return ProductKPIs(
        product_code=product_code,
        window_days=window_days,
        computed_at=moment,
        window_start=window_start,
        funnel=funnel,
        reply_rate=_ratio(funnel.replied, funnel.contacted),
        demo_rate=_ratio(funnel.demos_held, funnel.contacted),
        pilot_rate=_ratio(funnel.pilots_started, funnel.demos_held),
        qualified_organizations=funnel.qualified,
        verified_organizations=verifications.get(VerificationStatus.VERIFIED, 0),
        unknown_organizations=verifications.get(VerificationStatus.UNKNOWN, 0),
        notes=notes,
    )


def compute_all_kpis(
    session: Session, *, window_days: int = DEFAULT_WINDOW_DAYS, now: datetime | None = None
) -> list[ProductKPIs]:
    products = list(session.execute(select(Product).order_by(Product.code)).scalars())
    return [
        compute_product_kpis(
            session,
            product_id=product.id,
            product_code=product.code,
            window_days=window_days,
            now=now,
        )
        for product in products
    ]


def funnel_timeseries(
    session: Session,
    *,
    product_id: uuid.UUID,
    event_type: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Série journalière d'un type d'événement, jours vides inclus.

    Les jours sans événement sont remplis avec 0 : une courbe trouée se lit mal
    et laisse croire à une interruption de mesure.
    """
    moment = now or datetime.now(UTC)
    window_start = moment - timedelta(days=window_days)

    events = session.execute(
        select(OutreachEvent.occurred_at).where(
            OutreachEvent.product_id == product_id,
            OutreachEvent.event_type == event_type,
            OutreachEvent.occurred_at >= window_start,
            OutreachEvent.occurred_at <= moment,
        )
    ).scalars()

    buckets: dict[str, int] = {}
    for occurred_at in events:
        key = occurred_at.date().isoformat()
        buckets[key] = buckets.get(key, 0) + 1

    series: list[dict[str, Any]] = []
    for offset in range(window_days + 1):
        day = (window_start + timedelta(days=offset)).date().isoformat()
        series.append({"date": day, "count": buckets.get(day, 0)})
    return series
