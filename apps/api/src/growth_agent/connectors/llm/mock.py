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
        """Rédige un premier contact qui ne sonne pas comme une machine.

        Les règles appliquées ici sont celles qu'on donnerait à un commercial
        débutant : ouvrir sur ce qu'on a lu chez eux, dire en une phrase ce que
        fait le produit, demander une seule chose, signer de son nom.

        Ce qu'on s'interdit : se présenter avant de parler d'eux, coller une
        URL dans le texte, employer un superlatif, ou terminer par une formule
        de politesse sans personne derrière.
        """
        context = request.metadata
        product = context.get("product_name", "")
        org = context.get("organization_name", "votre établissement")
        need = (context.get("need_summary") or "").strip()
        source_title = (context.get("source_title") or "").strip()
        signature = (context.get("signature") or "").strip()
        product_code = str(context.get("product_code", "")).lower()

        pitch = _PITCH.get(product_code, f"{product} répond à ce type d'organisation.")
        demo = _DEMO.get(product_code, "je peux vous le montrer en 30 minutes")

        if need and source_title:
            # Cas favorable : on a lu quelque chose de précis chez eux. On
            # désigne la source par sa nature — « votre offre d'emploi » — et
            # non par son titre, qui contient souvent une ponctuation qui se
            # lit mal dans une phrase.
            subject = _subject_from_source(source_title, context.get("source_kind", ""))
            where = _SOURCE_PHRASE.get(context.get("source_kind", ""), "Sur votre site")
            # On cite entre guillemets au lieu d'enchâsser la citation dans la
            # phrase : une citation est rarement un complément grammatical
            # valide, et la citer telle quelle est à la fois plus juste et
            # plus convaincant — ce sont leurs mots.
            opening = f"{where}, j'ai relevé ceci : « {_as_quote(need)} »"
            ask = "Est-ce un sujet d'actualité chez vous ?"
        elif need:
            subject = _subject_from_need(need, org)
            opening = f"J'ai relevé ceci vous concernant : « {_as_quote(need)} »"
            ask = "Est-ce que le sujet est ouvert en ce moment ?"
        else:
            # Aucun signal : on ne feint pas d'en avoir un. On demande
            # simplement si on s'adresse à la bonne personne.
            subject = _subject_without_signal(product_code, org)
            opening = (
                f"Je cherche à savoir qui suit {_TOPIC.get(product_code, 'ce sujet')} chez {org}."
            )
            ask = "Est-ce que je m'adresse à la bonne personne ?"

        body = (
            f"Bonjour,\n\n"
            f"{opening}\n\n"
            f"{pitch} Si c'est utile, {demo}.\n\n"
            f"{ask}\n\n"
            f"Si ce n'est pas votre sujet, répondez STOP et je ne vous recontacte pas.\n\n"
            f"{signature}"
        )

        return {
            "subject": subject,
            "body": body,
            # On ne cite pas l'URL dans le texte : elle reste attachée au
            # brouillon pour que l'approbateur puisse vérifier la source.
            "evidence_refs": list(context.get("evidence_urls", [])),
            # On déclare les variables réellement substituées, nommées comme
            # dans les gabarits : c'est ce que le composeur confronte à la
            # liste blanche du gabarit.
            "variables_used": {
                "accroche": subject,
                "observation": opening,
                "signature": signature,
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


# --- Formulations par produit ------------------------------------------------
#
# Écrites à la main, produit par produit. Une phrase générique appliquée aux
# deux produits se reconnaîtrait immédiatement.

_PITCH: dict[str, str] = {
    "maintrix": (
        "Maintrix sert à suivre les interventions et les arrêts machine sans passer par un tableur."
    ),
    "educore": (
        "EduCore regroupe les inscriptions, la scolarité et les absences dans un seul outil."
    ),
}

_DEMO: dict[str, str] = {
    "maintrix": "je vous montre le suivi d'un arrêt sur un de vos équipements, en 30 minutes",
    "educore": "je vous montre une inscription d'élève sur vos propres cas, en 30 minutes",
}

_TOPIC: dict[str, str] = {
    "maintrix": "la maintenance",
    "educore": "la gestion administrative",
}


#: Comment désigner la source dans une phrase, selon sa nature. Nommer la
#: page évite de coller une URL, et « votre offre d'emploi » se lit mieux
#: qu'un titre brut contenant deux-points ou parenthèses.
_SOURCE_PHRASE: dict[str, str] = {
    "job_posting": "En lisant votre offre d'emploi",
    "press_article": "Dans un article qui vous est consacré",
    "public_website": "Sur votre site",
    "public_registry": "Sur votre fiche d'entreprise",
    "directory": "Dans l'annuaire où vous figurez",
    "partner_referral": "Par un contact commun",
    "manual_import": "En me renseignant sur vous",
}


def _as_quote(clause: str) -> str:
    """Prépare une citation : majuscule initiale, point final."""
    text = clause.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else f"{text}."


#: Les titres de presse sont des phrases : « Votre » devant donnerait
#: « Votre agro Douala investit dans ses lignes ». On ne le préfixe que pour
#: les pages qui leur appartiennent vraiment.
_POSSESSIVE_KINDS = frozenset({"public_website", "public_registry", "job_posting"})


def _subject_from_source(source_title: str, source_kind: str = "") -> str:
    """Objet construit sur le titre de leur propre page.

    Un deux-points dans le titre (« Offre : technicien… ») donnerait une
    phrase bancale ; on le remplace par un tiret, qui passe bien en objet.
    """
    title = source_title.strip().rstrip(".")
    if not title:
        return "Votre projet"
    if " : " in title:
        before, _, after = title.partition(" : ")
        return f"Votre {before.strip().lower()} — {after.strip()}"
    if source_kind in _POSSESSIVE_KINDS:
        return f"{_possessive(title)} {title[0].lower() + title[1:]}"
    return f"À propos de : {title}"


def _possessive(title: str) -> str:
    """« Votre » ou « Vos », selon que le premier mot est au pluriel.

    « Votre inscriptions 2026 » saute aux yeux ; une faute d'accord dans
    l'objet suffit à faire classer le message.
    """
    first = title.strip().split()[0] if title.strip() else ""
    stripped = first.rstrip(".,;:")
    # Les mots invariables en -s ou -x au singulier sont rares et identifiés.
    invariables = {"processus", "cours", "congres", "proces", "prix", "choix", "corpus"}
    import unicodedata

    plain = "".join(
        c for c in unicodedata.normalize("NFKD", stripped.lower()) if not unicodedata.combining(c)
    )
    if plain in invariables or len(plain) < 3:
        return "Votre"
    return "Vos" if plain.endswith(("s", "x")) else "Votre"


def _subject_from_need(need: str, org: str) -> str:
    words = need.strip().split()
    fragment = " ".join(words[:6]).rstrip(",.")
    return fragment[0].upper() + fragment[1:] if fragment else f"Question — {org}"


def _subject_without_signal(product_code: str, org: str) -> str:
    topic = {
        "maintrix": "la maintenance",
        "educore": "les inscriptions",
    }.get(product_code, "ce sujet")
    return f"Qui suit {topic} chez {org} ?"
