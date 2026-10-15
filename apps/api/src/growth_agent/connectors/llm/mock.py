"""Fournisseur LLM déterministe, hors ligne et gratuit.

Ce n'est pas un bouchon vide : il produit des sorties conformes aux schémas
attendus par les agents, à partir des données du prompt uniquement. C'est ce
qui permet de faire tourner la totalité des tests, y compris le parcours
commercial complet, sans clé d'API ni dépense.

Il est volontairement incapable d'inventer : si une information manque dans le
prompt, il renvoie `inconnu` plutôt qu'une valeur plausible.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from growth_agent.connectors.llm.base import (
    LLMCompletion,
    LLMProvider,
    LLMRequest,
    TokenBudget,
    estimate_tokens,
)


class MockLLMProvider(LLMProvider):
    provider = "mock"
    model_reference = "mock-deterministe-v1"

    def __init__(
        self,
        *,
        budget: TokenBudget | None = None,
        scripted_responses: list[str] | None = None,
    ) -> None:
        super().__init__(budget=budget)
        # Permet à un test d'imposer une réponse précise (y compris invalide,
        # pour vérifier la validation de schéma).
        self._scripted = list(scripted_responses or [])
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMCompletion:
        self.calls.append(request)
        input_tokens = estimate_tokens(request.system_prompt + request.user_prompt)
        self.budget.check(input_tokens + request.max_output_tokens)

        if self._scripted:
            text = self._scripted.pop(0)
        else:
            text = self._generate(request)

        output_tokens = estimate_tokens(text)
        self.budget.record(input_tokens + output_tokens)
        return LLMCompletion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_reference=self.model_reference,
            provider=self.provider,
        )

    # --- Génération ----------------------------------------------------------
    def _generate(self, request: LLMRequest) -> str:
        task = str(request.metadata.get("task", ""))
        handler = {
            "qualify": self._qualify,
            "compose": self._compose,
            "classify_reply": self._classify_reply,
            "summarize": self._summarize,
        }.get(task, self._fallback)
        return json.dumps(handler(request), ensure_ascii=False)

    def _qualify(self, request: LLMRequest) -> dict[str, Any]:
        """Qualification : ne s'appuie que sur ce qui est présent dans le prompt."""
        context = request.metadata
        evidence: list[str] = list(context.get("evidence_urls", []))
        has_evidence = bool(evidence)
        need_quotes = _find_need_quotes(request.user_prompt)
        return {
            "need_detected": bool(need_quotes),
            "need_summary": need_quotes[0] if need_quotes else "",
            "evidence_urls": evidence,
            "decision_maker_identified": bool(context.get("contact_role")),
            "pilot_feasible": has_evidence and bool(context.get("contact_email")),
            "confidence": "medium" if has_evidence else "low",
            "unknowns": [] if has_evidence else ["aucune source fournie"],
            "rationale": (
                "Évaluation fondée uniquement sur les sources citées dans le contexte."
                if has_evidence
                else "Aucune source exploitable : la plupart des critères restent inconnus."
            ),
        }

    def _compose(self, request: LLMRequest) -> dict[str, Any]:
        context = request.metadata
        product = context.get("product_name", "notre solution")
        org = context.get("organization_name", "votre organisation")
        role = context.get("contact_role") or "Bonjour"
        need = context.get("need_summary") or ""
        evidence = list(context.get("evidence_urls", []))

        need_line = (
            f"En consultant {evidence[0]}, j'ai noté : {need}."
            if need and evidence
            else "Je me permets de vous écrire au sujet de vos enjeux d'organisation."
        )
        body = (
            f"Bonjour,\n\n"
            f"Je m'adresse à vous en tant que {role.lower()} de {org}. {need_line}\n\n"
            f"{product} répond précisément à ce type de besoin. Seriez-vous disponible "
            f"pour une démonstration de 30 minutes sur un scénario vérifiable ?\n\n"
            f"Si ce message ne vous concerne pas, répondez simplement « STOP » : "
            f"votre adresse sera retirée de nos envois.\n\n"
            f"Bien cordialement,"
        )
        return {
            "subject": f"{product} — démonstration de 30 minutes pour {org}",
            "body": body,
            "evidence_refs": evidence,
            "variables_used": {
                "organization_name": org,
                "product_name": product,
                "contact_role": role,
            },
            "language": context.get("language", "fr"),
        }

    def _classify_reply(self, request: LLMRequest) -> dict[str, Any]:
        text = request.user_prompt.lower()
        rules: list[tuple[str, tuple[str, ...]]] = [
            ("opt_out", ("stop", "désinscri", "desinscri", "ne plus", "unsubscribe")),
            ("not_interested", ("pas intéress", "pas interess", "non merci", "refus")),
            (
                "interested",
                (
                    "intéress",
                    "interess",
                    "démonstration",
                    "demonstration",
                    "rendez-vous",
                    "disponible",
                ),
            ),
            ("question", ("combien", "quel", "comment", "pourquoi", "?")),
            ("out_of_office", ("absent", "congé", "conge", "vacances")),
            ("referral", ("mon collègue", "mon collegue", "contactez", "adressez-vous")),
        ]
        intent = "unclear"
        for label, needles in rules:
            if any(needle in text for needle in needles):
                intent = label
                break
        return {
            "intent": intent,
            "sentiment": "negative" if intent in {"opt_out", "not_interested"} else "neutral",
            "objections": [],
            "requires_human": intent in {"unclear", "question", "referral"},
            "confidence": "high" if intent != "unclear" else "low",
            "suggested_next_step": {
                "opt_out": "Ajouter le contact à la liste d'opposition immédiatement.",
                "interested": "Proposer trois créneaux de démonstration.",
                "not_interested": "Clore l'opportunité et ne pas relancer.",
            }.get(intent, "Faire relire la réponse par un commercial."),
        }

    def _summarize(self, request: LLMRequest) -> dict[str, Any]:
        sentences = [s.strip() for s in re.split(r"[.!?\n]", request.user_prompt) if s.strip()]
        return {"summary": " ".join(sentences[:3])[:600], "key_points": sentences[:5]}

    def _fallback(self, request: LLMRequest) -> dict[str, Any]:
        fingerprint = hashlib.sha256(request.user_prompt.encode()).hexdigest()[:12]
        return {
            "status": "unsupported_task",
            "message": "Le fournisseur mock ne connaît pas cette tâche.",
            "prompt_fingerprint": fingerprint,
        }


_NEED_PATTERNS = (
    re.compile(r"(recrute[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(modernis[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(digitalis[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(appel d'offres?[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(gmao[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(gestion (?:administrative|de maintenance)[^.\n]{0,80})", re.IGNORECASE),
    re.compile(r"(ouvre|ouverture)([^.\n]{0,80})", re.IGNORECASE),
)


def _find_need_quotes(text: str) -> list[str]:
    """Extrait des citations littérales du prompt — jamais de reformulation
    inventée : si rien ne correspond, la liste est vide."""
    found: list[str] = []
    for pattern in _NEED_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(match.group(0).strip())
    return found
