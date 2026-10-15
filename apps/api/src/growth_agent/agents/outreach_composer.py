"""Outreach Composer — rédaction de messages contextualisés.

Garde-fou : validation humaine avant envoi. L'agent ne produit QUE des
brouillons ; il n'a accès à aucun fournisseur d'email.

Trois contrôles sont appliqués au texte produit, avant même l'approbation :

* **ancrage** — toute affirmation sur le prospect doit s'appuyer sur une source
  rattachée ; les URL citées sont vérifiées contre les sources connues ;
* **variables** — seules les variables déclarées par le gabarit sont
  substituées ; une variable inconnue ou non résolue bloque le brouillon plutôt
  que de laisser partir un « Bonjour {{prenom}} » ;
* **mention d'opposition** — le message doit contenir un moyen de s'opposer,
  faute de quoi il est marqué bloqué.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from growth_agent.agents.base import AgentContext, AgentResult, BaseAgent
from growth_agent.connectors.llm.base import LLMProvider, LLMRequest, LLMSchemaError
from growth_agent.models.catalog import Campaign, Product
from growth_agent.models.enums import (
    AgentName,
    DraftStatus,
    LegalBasis,
    OutreachChannel,
    VerificationStatus,
)
from growth_agent.models.outreach import MessageDraft, MessageTemplate
from growth_agent.models.prospect import ProspectCandidate, ProspectSource
from growth_agent.services.audit import record_audit
from growth_agent.services.suppression import check_suppression

TEMPLATE_VARIABLE_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")

OPT_OUT_MARKERS = ("stop", "désinscri", "desinscri", "opposition", "ne plus recevoir")

SYSTEM_PROMPT = (
    "Tu rédiges un premier message de prospection B2B en français, sobre et court. "
    "Tu n'affirmes QUE ce qui figure dans le contexte fourni. "
    "Tu n'inventes aucun chiffre, aucune référence client, aucun engagement de prix. "
    "Tu n'emploies pas de superlatif ni de promesse de résultat. "
    "Le message doit permettre au destinataire de s'opposer facilement. "
    "Réponds uniquement par un objet JSON conforme au schéma demandé."
)


class ComposedMessage(BaseModel):
    subject: str = ""
    body: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    variables_used: dict[str, str] = Field(default_factory=dict)
    language: str = "fr"


class CompositionRefused(Exception):
    """La rédaction est refusée en amont : le prospect n'est pas sollicitable."""


@dataclass(slots=True)
class CompositionOutcome:
    draft: MessageDraft | None
    blocked_reason: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.draft is None or self.draft.status == DraftStatus.BLOCKED


class OutreachComposerAgent(BaseAgent[CompositionOutcome]):
    name = AgentName.OUTREACH_COMPOSER
    guardrail = "Produit uniquement des brouillons ; aucun envoi sans validation humaine."

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def run(  # type: ignore[override]
        self,
        context: AgentContext,
        *,
        prospect: ProspectCandidate,
        product: Product,
        campaign: Campaign | None = None,
        template: MessageTemplate | None = None,
        **_: Any,
    ) -> AgentResult[CompositionOutcome]:
        warnings: list[str] = []

        # Refus en amont — inutile de rédiger ce qui ne pourra pas partir.
        if prospect.verification_status == VerificationStatus.REJECTED:
            raise CompositionRefused("Prospect écarté du profil cible : pas de rédaction.")
        if prospect.verification_status == VerificationStatus.UNKNOWN:
            raise CompositionRefused(
                "Prospect non vérifié : qualifier et documenter les sources avant de rédiger."
            )
        verdict = check_suppression(
            context.session,
            email=prospect.contact_email,
            product_id=product.id,
            now=context.moment,
        )
        if verdict.suppressed:
            raise CompositionRefused(
                f"Contact opposé ou destinataire invalide (motif « {verdict.reason} »)."
            )
        if template is not None and template.product_id != product.id:
            raise CompositionRefused(
                "Gabarit rattaché à un autre produit : mélange Maintrix/EduCore refusé."
            )

        sources = list(
            context.session.execute(
                select(ProspectSource).where(ProspectSource.prospect_id == prospect.id)
            ).scalars()
        )
        known_urls = {s.url for s in sources}
        need_summary = _latest_need_summary(context, prospect)

        composed, model_reference = self._compose(
            prospect=prospect,
            product=product,
            need_summary=need_summary,
            known_urls=sorted(known_urls),
            template=template,
        )
        if composed is None:
            raise CompositionRefused("Le modèle n'a pas produit de message conforme au schéma.")

        subject = composed.subject.strip()
        body = composed.body.strip()
        blocked_reason = ""

        # 1. Variables non résolues ------------------------------------------
        unresolved = sorted(set(TEMPLATE_VARIABLE_RE.findall(f"{subject} {body}")))
        if unresolved:
            blocked_reason = (
                "Variables non résolues dans le message : " + ", ".join(unresolved) + "."
            )

        # 2. Variables hors liste blanche ------------------------------------
        if not blocked_reason and template is not None and template.allowed_variables:
            allowed = {str(v) for v in template.allowed_variables}
            used = set(composed.variables_used)
            unexpected = sorted(used - allowed)
            if unexpected:
                blocked_reason = (
                    "Variables non autorisées par le gabarit : " + ", ".join(unexpected) + "."
                )

        # 3. URL citées mais inconnues ---------------------------------------
        if not blocked_reason:
            cited = set(URL_RE.findall(body))
            invented = sorted(u for u in cited if u.rstrip(".,;") not in known_urls)
            if invented:
                blocked_reason = (
                    "Le message cite une source non rattachée au prospect : "
                    + ", ".join(invented[:3])
                    + "."
                )

        # 4. Moyen d'opposition ----------------------------------------------
        if not blocked_reason and not _mentions_opt_out(body):
            blocked_reason = "Le message ne propose aucun moyen de s'opposer à la prospection."

        draft = MessageDraft(
            product_id=product.id,
            campaign_id=campaign.id if campaign else None,
            prospect_id=prospect.id,
            template_id=template.id if template else None,
            channel=OutreachChannel.EMAIL,
            recipient_email=prospect.contact_email,
            subject=subject[:512],
            body=body,
            language=composed.language or "fr",
            status=DraftStatus.BLOCKED if blocked_reason else DraftStatus.DRAFT,
            variables_used=dict(composed.variables_used),
            evidence_refs=[u for u in composed.evidence_refs if u in known_urls],
            generated_by=self.name,
            model_reference=model_reference,
            blocked_reason=blocked_reason,
        )
        context.session.add(draft)
        context.session.flush()

        if prospect.legal_basis == LegalBasis.UNDETERMINED:
            warnings.append(
                "Base légale non déterminée pour ce contact : à trancher avant approbation."
            )
        if blocked_reason:
            warnings.append(blocked_reason)

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="outreach_composer.drafted",
            object_type="message_draft",
            object_id=draft.id,
            product_code=product.code,
            outcome="blocked" if blocked_reason else "success",
            metadata={"blocked_reason": blocked_reason, "template": template.slug if template else ""},
            now=context.moment,
        )

        return AgentResult(
            value=CompositionOutcome(draft=draft, blocked_reason=blocked_reason),
            summary={"draft_id": str(draft.id), "status": draft.status},
            model_reference=model_reference,
            warnings=warnings,
        )

    def _compose(
        self,
        *,
        prospect: ProspectCandidate,
        product: Product,
        need_summary: str,
        known_urls: list[str],
        template: MessageTemplate | None,
    ) -> tuple[ComposedMessage | None, str]:
        template_hint = ""
        if template is not None:
            template_hint = (
                f"\n\nGabarit à suivre (variables autorisées : "
                f"{', '.join(str(v) for v in template.allowed_variables) or 'aucune'}) :\n"
                f"Objet : {template.subject_template}\n{template.body_template}"
            )

        request = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=(
                f"Produit : {product.name} — {product.description}\n"
                f"Organisation : {prospect.organization_name}\n"
                f"Fonction du contact : {prospect.contact_role or 'inconnue'}\n"
                f"Besoin identifié : {need_summary or 'aucun besoin documenté'}\n"
                f"Sources disponibles : {', '.join(known_urls) or 'aucune'}"
                f"{template_hint}"
            ),
            metadata={
                "task": "compose",
                "product_name": product.name,
                "organization_name": prospect.organization_name,
                "contact_role": prospect.contact_role,
                "need_summary": need_summary,
                "evidence_urls": known_urls,
                "language": "fr",
            },
        )
        try:
            return self._llm.complete_structured(request, ComposedMessage), self._llm.model_reference
        except (LLMSchemaError, Exception):
            return None, self._llm.model_reference


def _latest_need_summary(context: AgentContext, prospect: ProspectCandidate) -> str:
    from growth_agent.models.prospect import QualificationAssessment

    assessment = context.session.execute(
        select(QualificationAssessment)
        .where(QualificationAssessment.prospect_id == prospect.id)
        .order_by(QualificationAssessment.created_at.desc())
    ).scalars().first()
    if assessment is None:
        return ""
    for criterion in assessment.criteria:
        if criterion.get("key") == "documented_need" and criterion.get("points", 0) > 0:
            return str(criterion.get("comment", ""))
    return ""


def _mentions_opt_out(body: str) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in OPT_OUT_MARKERS)
