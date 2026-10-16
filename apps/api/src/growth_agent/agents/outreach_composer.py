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
from growth_agent.config import Settings, get_settings
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
from growth_agent.services.opt_out_language import closing_line, mentions_opt_out
from growth_agent.services.suppression import check_suppression
from growth_agent.services.writing import StyleReport, analyze_message

TEMPLATE_VARIABLE_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")


SYSTEM_PROMPT_TEMPLATE = """Tu écris le premier message d'un commercial à un
prospect qu'il ne connaît pas. En français, et de façon à ce qu'on ne
devine pas qu'une machine l'a rédigé.

La structure, dans cet ordre :
1. Ce que tu as lu chez eux. Un fait précis, pas une généralité.
2. Ce que fait le produit, en une phrase, avec un verbe concret.
3. Une seule demande, peu engageante.
4. Une sortie : comment ne plus être contacté.
5. La signature fournie, telle quelle.

Ce qui trahit immédiatement une machine, et que tu n'écris donc jamais :
- « Je me permets de vous contacter », « J'espère que ce message vous trouve bien »
- « Je m'adresse à vous en tant que [fonction] de [organisation] » : il connaît
  sa propre fonction
- « solution innovante », « leader », « incontournable », « clé en main »,
  « parfaitement adapté à vos besoins », « répond précisément à ce type de besoin »
- « N'hésitez pas à », « Je reste à votre entière disposition »
- une URL collée dans le texte : nomme la page (« votre projet d'établissement »)
- des connecteurs empilés (« De plus », « En outre », « Par ailleurs »)
- des phrases toutes de la même longueur : alterne long et court
- une formule de politesse sans nom derrière

Contraintes de fond :
- Tu n'affirmes que ce qui figure dans le contexte fourni. Aucun chiffre,
  aucune référence client, aucun engagement de prix inventé.
- Si aucun besoin n'est documenté, ne fais pas semblant : demande simplement
  si tu t'adresses à la bonne personne. C'est plus honnête et ça marche mieux.
- 60 à 110 mots. Au-delà, le message n'est pas lu.
- Pas de liste à puces dans un premier contact.

Exemple de ce qu'on attend :

    Bonjour,

    En lisant votre projet d'établissement 2026, j'ai noté que vous comptiez
    digitaliser le suivi des absences.

    EduCore regroupe inscriptions, scolarité et absences dans un seul outil.
    Si c'est utile, je vous montre une inscription sur vos propres cas, en
    30 minutes.

    Est-ce un sujet d'actualité chez vous ?

    {ligne_opposition}

    Awa Njoya
    TechLearn

Réponds uniquement par un objet JSON conforme au schéma demandé."""


def system_prompt(*, replies_are_monitored: bool) -> str:
    """Le prompt, avec la seule ligne qui dépend de la configuration.

    Quand aucune boîte ne reçoit les réponses, le message ne doit pas inviter
    à répondre : il renvoie au lien de désinscription, qui, lui, fonctionne.
    """
    return SYSTEM_PROMPT_TEMPLATE.replace(
        "{ligne_opposition}", closing_line(replies_are_monitored=replies_are_monitored)
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
    style: StyleReport | None = None

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
        settings: Settings | None = None,
        **_: Any,
    ) -> AgentResult[CompositionOutcome]:
        warnings: list[str] = []
        settings = settings or get_settings()

        # Un message doit être signé par quelqu'un. Plutôt que de produire un
        # texte anonyme qu'il faudra corriger, on refuse de rédiger.
        if not settings.sender_name.strip():
            raise CompositionRefused(
                "Aucun expéditeur configuré (GA_SENDER_NAME). Un message de "
                "prospection se signe d'un nom : sans lui, le destinataire voit "
                "immédiatement un envoi automatique."
            )

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
            source_title=sources[0].title if sources else "",
            source_kind=sources[0].kind if sources else "",
            signature=_signature(settings),
            replies_are_monitored=settings.replies_are_monitored(),
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
        if not blocked_reason and not mentions_opt_out(body):
            blocked_reason = "Le message ne propose aucun moyen de s'opposer à la prospection."

        # 5. Naturel du texte ---------------------------------------------------
        # Un message qui sonne artificiel n'est pas un défaut de conformité mais
        # il sera ignoré, ce qui revient au même. Les clichés rédhibitoires et
        # l'absence de signature bloquent ; le reste alerte l'approbateur.
        style = analyze_message(
            body,
            subject=subject,
            organization_name=prospect.organization_name,
            product_name=product.name,
        )
        if not blocked_reason and style.is_blocking:
            blocked_reason = " ".join(
                f"{f.label} : « {f.excerpt} »." for f in style.blocking_findings
            )

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
        for finding in style.findings:
            if finding.severity != "blocking":
                warnings.append(f"{finding.label} : « {finding.excerpt} » — {finding.suggestion}")

        record_audit(
            context.session,
            actor=context.actor,
            actor_role=context.actor_role,
            action="outreach_composer.drafted",
            object_type="message_draft",
            object_id=draft.id,
            product_code=product.code,
            outcome="blocked" if blocked_reason else "success",
            metadata={
                "blocked_reason": blocked_reason,
                "template": template.slug if template else "",
            },
            now=context.moment,
        )

        return AgentResult(
            value=CompositionOutcome(draft=draft, blocked_reason=blocked_reason, style=style),
            summary={
                "draft_id": str(draft.id),
                "status": draft.status,
                "naturalness": style.naturalness,
            },
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
        source_title: str,
        source_kind: str,
        signature: str,
        template: MessageTemplate | None,
        replies_are_monitored: bool,
    ) -> tuple[ComposedMessage | None, str]:
        template_hint = ""
        if template is not None:
            template_hint = (
                f"\n\nGabarit à suivre (variables autorisées : "
                f"{', '.join(str(v) for v in template.allowed_variables) or 'aucune'}) :\n"
                f"Objet : {template.subject_template}\n{template.body_template}"
            )

        request = LLMRequest(
            system_prompt=system_prompt(replies_are_monitored=replies_are_monitored),
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
                "product_code": product.code,
                "organization_name": prospect.organization_name,
                "contact_role": prospect.contact_role,
                "need_summary": need_summary,
                "source_title": source_title,
                "source_kind": source_kind,
                "signature": signature,
                "evidence_urls": known_urls,
                # Transmise explicitement : un fournisseur hors ligne ne relit
                # pas le prompt, et doit pourtant produire la bonne ligne.
                "closing_line": closing_line(replies_are_monitored=replies_are_monitored),
                "language": "fr",
            },
        )
        try:
            return self._llm.complete_structured(
                request, ComposedMessage
            ), self._llm.model_reference
        except (LLMSchemaError, Exception):
            return None, self._llm.model_reference


def _latest_need_summary(context: AgentContext, prospect: ProspectCandidate) -> str:
    from growth_agent.models.prospect import QualificationAssessment

    assessment = (
        context.session.execute(
            select(QualificationAssessment)
            .where(QualificationAssessment.prospect_id == prospect.id)
            .order_by(QualificationAssessment.created_at.desc())
        )
        .scalars()
        .first()
    )
    if assessment is None:
        return ""
    for criterion in assessment.criteria:
        if criterion.get("key") != "documented_need":
            continue
        # Les citations sont les mots du prospect ; le commentaire décrit le
        # critère pour un lecteur interne. Reprendre le commentaire dans un
        # message donnait « j'ai noté que 3 signal(aux) de besoin cité(s) ».
        quotes = [str(q) for q in (criterion.get("quotes") or []) if q]
        if quotes:
            return _best_quote(quotes)
    return ""


def _best_quote(quotes: list[str]) -> str:
    """Choisit la citation la plus présentable.

    Les signaux repérés par expression régulière commencent souvent au milieu
    d'une phrase (« recrute pour accompagner… ») là où la phrase entière de la
    source se lit seule. On retient donc la plus complète, bornée pour ne pas
    déverser un paragraphe dans un email.
    """
    usable = [q.strip() for q in quotes if 20 <= len(q.strip()) <= 220]
    return max(usable, key=len) if usable else quotes[0].strip()[:220]


def _signature(settings: Settings) -> str:
    """Signature de l'expéditeur, telle qu'elle apparaîtra en bas du message.

    Prénom et nom, puis la fonction si elle est renseignée, puis
    l'organisation. Pas de formule de politesse : elle appartient au corps du
    message, et l'y dupliquer sonne faux.
    """
    lines = [settings.sender_name.strip()]
    if settings.sender_role.strip():
        lines.append(settings.sender_role.strip())
    if settings.sender_organization.strip():
        lines.append(settings.sender_organization.strip())
    return "\n".join(line for line in lines if line)
