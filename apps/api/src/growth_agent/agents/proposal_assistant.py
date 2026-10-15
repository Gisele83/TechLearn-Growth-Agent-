"""Proposal Assistant — propositions et devis.

Garde-fou : prix et remises issus de règles approuvées.

Aucun prix n'est calculé par un modèle de langage. Les tarifs viennent d'un
catalogue versionné (`CATALOG`), et toute remise dépassant le plafond autorisé
déclenche une demande d'approbation distincte plutôt qu'un arrondi silencieux.
Tant que le catalogue réel n'est pas arrêté (voir docs/10), les montants
ci-dessous sont des valeurs de démonstration explicitement marquées comme
telles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.models.catalog import Product
from growth_agent.models.enums import AgentName, ApprovalSubject
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.services.approvals import request_approval
from growth_agent.services.audit import record_audit

#: Catalogue de démonstration. À remplacer par la grille validée — voir
#: docs/10-decisions-a-confirmer.md, décision « grilles tarifaires ».
CATALOG: dict[str, dict[str, Any]] = {
    "maintrix": {
        "currency": "EUR",
        "is_provisional": True,
        "plans": {
            "pilote": {"label": "Pilote 3 mois", "unit_price": 0, "unit": "forfait"},
            "standard": {"label": "Licence standard", "unit_price": 45, "unit": "utilisateur/mois"},
            "etendu": {"label": "Licence étendue", "unit_price": 75, "unit": "utilisateur/mois"},
        },
    },
    "educore": {
        "currency": "EUR",
        "is_provisional": True,
        "plans": {
            "pilote": {"label": "Pilote année scolaire", "unit_price": 0, "unit": "forfait"},
            "standard": {"label": "Licence établissement", "unit_price": 2.5, "unit": "élève/mois"},
            "etendu": {"label": "Licence multi-sites", "unit_price": 4, "unit": "élève/mois"},
        },
    },
}

#: Remise maximale applicable sans approbation dédiée.
MAX_AUTOMATIC_DISCOUNT_PCT = Decimal("10")


class PricingError(Exception):
    """Plan inconnu, quantité invalide ou remise hors règles."""


@dataclass(slots=True)
class QuoteLine:
    label: str
    unit: str
    quantity: int
    unit_price: Decimal
    subtotal: Decimal


@dataclass(slots=True)
class Quote:
    product_code: str
    currency: str
    lines: list[QuoteLine] = field(default_factory=list)
    discount_pct: Decimal = Decimal("0")
    total_before_discount: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    requires_discount_approval: bool = False
    is_provisional: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_code": self.product_code,
            "currency": self.currency,
            "lines": [
                {
                    "label": line.label,
                    "unit": line.unit,
                    "quantity": line.quantity,
                    "unit_price": float(line.unit_price),
                    "subtotal": float(line.subtotal),
                }
                for line in self.lines
            ],
            "discount_pct": float(self.discount_pct),
            "total_before_discount": float(self.total_before_discount),
            "total": float(self.total),
            "requires_discount_approval": self.requires_discount_approval,
            "is_provisional": self.is_provisional,
            "notes": list(self.notes),
        }


class ProposalAssistantAgent(BaseAgent[Quote]):
    name = AgentName.PROPOSAL_ASSISTANT
    guardrail = "Prix et remises issus du catalogue approuvé, jamais d'un modèle."

    def run(  # type: ignore[override]
        self,
        context: AgentContext,
        *,
        product: Product,
        prospect: ProspectCandidate,
        plan: str,
        quantity: int,
        discount_pct: float = 0.0,
        **_: Any,
    ) -> AgentResult[Quote]:
        catalog = CATALOG.get(product.code)
        if catalog is None:
            raise PricingError(f"Aucun catalogue pour le produit « {product.code} ».")
        plan_entry = catalog["plans"].get(plan)
        if plan_entry is None:
            raise PricingError(
                f"Plan « {plan} » inconnu. Plans disponibles : "
                + ", ".join(sorted(catalog["plans"]))
            )
        if quantity <= 0:
            raise PricingError("La quantité doit être strictement positive.")

        discount = Decimal(str(discount_pct))
        if discount < 0 or discount > 100:
            raise PricingError("La remise doit être comprise entre 0 et 100 %.")

        unit_price = Decimal(str(plan_entry["unit_price"]))
        subtotal = _money(unit_price * quantity)
        line = QuoteLine(
            label=plan_entry["label"],
            unit=plan_entry["unit"],
            quantity=quantity,
            unit_price=unit_price,
            subtotal=subtotal,
        )
        total = _money(subtotal * (Decimal("100") - discount) / Decimal("100"))

        quote = Quote(
            product_code=product.code,
            currency=catalog["currency"],
            lines=[line],
            discount_pct=discount,
            total_before_discount=subtotal,
            total=total,
            requires_discount_approval=discount > MAX_AUTOMATIC_DISCOUNT_PCT,
            is_provisional=bool(catalog.get("is_provisional", True)),
        )
        if quote.is_provisional:
            quote.notes.append(
                "Grille tarifaire provisoire : montants à confirmer avant toute "
                "transmission au prospect."
            )
        if quote.requires_discount_approval:
            quote.notes.append(
                f"Remise de {discount} % supérieure au plafond de "
                f"{MAX_AUTOMATIC_DISCOUNT_PCT} % : approbation distincte requise."
            )

        # Un devis ne part jamais sans validation ; la demande est créée ici.
        approval = request_approval(
            context.session,
            subject_type=ApprovalSubject.QUOTE,
            subject_id=prospect.id,
            requested_by=context.actor,
            summary=(
                f"Devis {product.name} — {plan} × {quantity} = {total} {quote.currency}"
                + (f" (remise {discount} %)" if discount else "")
            ),
            product_id=product.id,
            now=context.moment,
        )
        if quote.requires_discount_approval:
            request_approval(
                context.session,
                subject_type=ApprovalSubject.DISCOUNT,
                subject_id=prospect.id,
                requested_by=context.actor,
                summary=f"Remise exceptionnelle de {discount} % sur {product.name}.",
                product_id=product.id,
                now=context.moment,
            )

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="proposal_assistant.quote_prepared",
            object_type="prospect_candidate",
            object_id=prospect.id,
            product_code=product.code,
            metadata={
                "plan": plan,
                "quantity": quantity,
                "discount_pct": float(discount),
                "approval_id": str(approval.id),
            },
            now=context.moment,
        )
        return AgentResult(
            value=quote,
            summary={"total": float(total), "approval_id": str(approval.id)},
        )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
