"""Interface LLM.

Trois garanties imposées à toute implémentation :
  1. la sortie est validée par un modèle Pydantic avant d'être rendue — un JSON
     mal formé ou hors schéma est une erreur, jamais une valeur par défaut ;
  2. la consommation de jetons est comptée et plafonnée par un budget ;
  3. les contenus externes sont déjà assainis par `security.prompt_guard` avant
     d'arriver ici, et le prompt système rappelle qu'ils sont des données.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from growth_agent.connectors.base import ConnectorError

ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMSchemaError(ConnectorError):
    """La réponse du modèle ne respecte pas le schéma attendu."""


class LLMBudgetExceeded(ConnectorError):
    """Le budget de jetons est épuisé : l'appel est refusé avant émission."""


@dataclass(slots=True)
class LLMRequest:
    system_prompt: str
    user_prompt: str
    max_output_tokens: int = 1200
    temperature: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCompletion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model_reference: str = ""
    provider: str = ""


@dataclass(slots=True)
class TokenBudget:
    """Budget de jetons par journée glissante, vérifié avant chaque appel."""

    daily_limit: int
    consumed: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.daily_limit - self.consumed)

    def check(self, projected_tokens: int) -> None:
        if self.daily_limit <= 0:
            return
        if self.consumed + projected_tokens > self.daily_limit:
            raise LLMBudgetExceeded(
                f"Budget LLM épuisé : {self.consumed}/{self.daily_limit} jetons consommés, "
                f"{projected_tokens} demandés."
            )

    def record(self, tokens: int) -> None:
        self.consumed += max(0, tokens)

    def reset(self) -> None:
        self.consumed = 0


def estimate_tokens(text: str) -> int:
    """Estimation grossière (≈4 caractères par jeton).

    Suffisante pour un garde-fou de budget : on préfère surestimer et refuser
    un appel que découvrir le dépassement sur la facture.
    """
    return max(1, len(text) // 4)


class LLMProvider(ABC):
    provider: str = "abstract"
    model_reference: str = ""

    def __init__(self, *, budget: TokenBudget | None = None) -> None:
        self.budget = budget or TokenBudget(daily_limit=0)

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMCompletion:
        """Appel brut. Les appelants devraient préférer `complete_structured`."""

    def complete_structured(
        self, request: LLMRequest, schema: type[ModelT], *, max_attempts: int = 2
    ) -> ModelT:
        """Appelle le modèle et valide la réponse contre `schema`.

        Un second essai est accordé avec une consigne de correction explicite ;
        au-delà, l'erreur remonte. On ne « répare » jamais silencieusement une
        sortie invalide : une donnée inventée coûte plus cher qu'un échec.
        """
        last_error: Exception | None = None
        current = request
        for attempt in range(max_attempts):
            completion = self.complete(current)
            payload = _extract_json(completion.text)
            if payload is None:
                last_error = LLMSchemaError(
                    "Aucun objet JSON exploitable dans la réponse du modèle.",
                    provider=self.provider,
                )
            else:
                try:
                    return schema.model_validate(payload)
                except ValidationError as exc:
                    last_error = LLMSchemaError(
                        f"Réponse hors schéma : {exc.error_count()} erreur(s).",
                        provider=self.provider,
                    )
            if attempt + 1 < max_attempts:
                current = LLMRequest(
                    system_prompt=request.system_prompt,
                    user_prompt=(
                        f"{request.user_prompt}\n\n"
                        "La réponse précédente était invalide. Réponds UNIQUEMENT par un objet "
                        f"JSON conforme à ce schéma : {schema.model_json_schema()}"
                    ),
                    max_output_tokens=request.max_output_tokens,
                    temperature=0.0,
                    metadata=request.metadata,
                )
        raise last_error or LLMSchemaError("Échec de validation.", provider=self.provider)

    def healthcheck(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_reference": self.model_reference,
            "budget_remaining": self.budget.remaining,
        }


def _extract_json(text: str) -> Any | None:
    """Extrait le premier objet JSON équilibré d'une réponse.

    Les modèles encadrent souvent leur JSON de texte ou de blocs ```json.
    On cherche donc le premier `{` et son accolade fermante correspondante, en
    ignorant les accolades situées à l'intérieur d'une chaîne.
    """
    import json

    if not text:
        return None
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except (ValueError, TypeError):
        pass

    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stripped[start : index + 1])
                except (ValueError, TypeError):
                    return None
    return None
