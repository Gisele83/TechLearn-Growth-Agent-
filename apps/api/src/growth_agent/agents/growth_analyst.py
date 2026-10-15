"""Growth Analyst — mesure du funnel et des performances.

Garde-fou : indicateurs reproductibles et datés.

L'agent ne stocke aucun agrégat : il recalcule tout à partir des événements, et
chaque restitution porte sa date de calcul et sa fenêtre. Quand une base de
calcul est vide, il renvoie `null` et une note explicite plutôt qu'un zéro qui
se lirait comme une contre-performance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.models.catalog import Product
from growth_agent.models.enums import AgentName
from growth_agent.services.analytics import ProductKPIs, compute_product_kpis
from growth_agent.services.audit import record_audit

#: Objectifs exploratoires à 90 jours issus du cahier des charges. Ce sont des
#: cibles de travail, pas des engagements.
EXPLORATORY_TARGETS: dict[str, dict[str, int]] = {
    "maintrix": {"qualified_organizations": 50, "demos_held": 10, "pilots_started": 3},
    "educore": {"qualified_organizations": 100, "demos_held": 15, "pilots_started": 5},
}


@dataclass(slots=True)
class GrowthReport:
    kpis: list[ProductKPIs] = field(default_factory=list)
    target_gaps: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kpis": [k.as_dict() for k in self.kpis],
            "target_gaps": list(self.target_gaps),
            "recommendations": list(self.recommendations),
        }


class GrowthAnalystAgent(BaseAgent[GrowthReport]):
    name = AgentName.GROWTH_ANALYST
    guardrail = "Indicateurs recalculés depuis les événements, datés, jamais extrapolés."

    def run(  # type: ignore[override]
        self, context: AgentContext, *, window_days: int = 90, **_: Any
    ) -> AgentResult[GrowthReport]:
        products = list(
            context.session.execute(select(Product).order_by(Product.code)).scalars()
        )
        report = GrowthReport()

        for product in products:
            kpis = compute_product_kpis(
                context.session,
                product_id=product.id,
                product_code=product.code,
                window_days=window_days,
                now=context.moment,
            )
            report.kpis.append(kpis)

            targets = EXPLORATORY_TARGETS.get(product.code, {})
            actuals = {
                "qualified_organizations": kpis.qualified_organizations,
                "demos_held": kpis.funnel.demos_held,
                "pilots_started": kpis.funnel.pilots_started,
            }
            for metric, target in targets.items():
                actual = actuals.get(metric, 0)
                report.target_gaps.append(
                    {
                        "product_code": product.code,
                        "metric": metric,
                        "target": target,
                        "actual": actual,
                        "gap": target - actual,
                        "attained": actual >= target,
                        "note": "Cible exploratoire, non contractuelle.",
                    }
                )

            report.recommendations.extend(_recommend(product.code, kpis))

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="growth_analyst.report",
            object_type="report",
            metadata={"window_days": window_days, "products": len(products)},
            now=context.moment,
        )
        return AgentResult(
            value=report,
            summary={"products": len(products), "window_days": window_days},
        )


def _recommend(product_code: str, kpis: ProductKPIs) -> list[str]:
    """Recommandations fondées sur les seuls chiffres observés.

    Aucune recommandation n'est émise quand la base de calcul est trop faible :
    conseiller sur trois envois serait du bruit présenté comme une analyse.
    """
    out: list[str] = []
    funnel = kpis.funnel

    if funnel.contacted < 10:
        out.append(
            f"[{product_code}] Trop peu d'envois ({funnel.contacted}) pour conclure quoi que ce "
            "soit sur la performance : poursuivre la collecte avant d'arbitrer."
        )
        return out

    if kpis.reply_rate is not None and kpis.reply_rate < 0.05:
        out.append(
            f"[{product_code}] Taux de réponse de {round(kpis.reply_rate * 100, 1)} % sur "
            f"{funnel.contacted} envois : revoir le ciblage et l'accroche avant d'augmenter "
            "les volumes."
        )
    if funnel.opted_out and funnel.contacted and funnel.opted_out / funnel.contacted > 0.02:
        out.append(
            f"[{product_code}] {funnel.opted_out} opposition(s) pour {funnel.contacted} envois : "
            "vérifier la pertinence du ciblage, le volume n'est pas en cause."
        )
    if funnel.blocked:
        out.append(
            f"[{product_code}] {funnel.blocked} envoi(s) bloqué(s) par un garde-fou : "
            "examiner le journal d'audit, ces messages n'ont pas atteint leur destinataire."
        )
    if kpis.unknown_organizations > kpis.verified_organizations:
        out.append(
            f"[{product_code}] {kpis.unknown_organizations} organisation(s) non vérifiée(s) contre "
            f"{kpis.verified_organizations} vérifiée(s) : l'effort de sourcing précède l'effort "
            "d'envoi."
        )
    return out
