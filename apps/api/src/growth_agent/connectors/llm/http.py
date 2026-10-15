"""Fournisseur LLM HTTP générique (API compatible OpenAI / chat completions).

Non figé sur un éditeur : l'URL de base, le modèle et la clé viennent de la
configuration, ce qui permet de changer de fournisseur sans toucher au code des
agents. Jamais instancié tant que `GA_LLM_PROVIDER` vaut `mock`.

Non vérifié contre un fournisseur réel dans ce dépôt : la forme de la réponse
doit être confirmée lors du premier branchement.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from growth_agent.connectors.base import (
    CircuitBreaker,
    ConnectorError,
    RateLimitError,
    backoff_delays,
)
from growth_agent.connectors.llm.base import (
    LLMCompletion,
    LLMProvider,
    LLMRequest,
    TokenBudget,
    estimate_tokens,
)


class HTTPLLMProvider(LLMProvider):
    provider = "http"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        budget: TokenBudget | None = None,
        max_input_tokens: int = 6000,
        max_retries: int = 3,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        super().__init__(budget=budget)
        if not (base_url and api_key and model):
            raise ConnectorError(
                "Fournisseur LLM HTTP incomplet (URL, clé ou modèle manquant).",
                provider=self.provider,
            )
        self.model_reference = model
        self._max_input_tokens = max_input_tokens
        self._max_retries = max_retries
        self._sleep = sleep
        self._breaker = CircuitBreaker()
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def complete(self, request: LLMRequest) -> LLMCompletion:
        input_tokens = estimate_tokens(request.system_prompt + request.user_prompt)
        if input_tokens > self._max_input_tokens:
            raise ConnectorError(
                f"Prompt trop long : {input_tokens} jetons estimés pour un plafond de "
                f"{self._max_input_tokens}.",
                provider=self.provider,
            )
        self.budget.check(input_tokens + request.max_output_tokens)

        payload = {
            "model": self.model_reference,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }

        delays = backoff_delays(self._max_retries)
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._breaker.ensure_closed(provider=self.provider)
            try:
                response = self._client.post("/v1/chat/completions", json=payload)
            except httpx.HTTPError as exc:
                self._breaker.record_failure()
                last_error = ConnectorError(
                    f"Erreur réseau LLM : {type(exc).__name__}",
                    provider=self.provider,
                    retryable=True,
                )
            else:
                if response.status_code == 429:
                    self._breaker.record_failure()
                    last_error = RateLimitError("Quota LLM atteint.", provider=self.provider)
                elif 500 <= response.status_code < 600:
                    self._breaker.record_failure()
                    last_error = ConnectorError(
                        f"Le fournisseur LLM a renvoyé {response.status_code}.",
                        provider=self.provider,
                        retryable=True,
                    )
                elif response.status_code >= 400:
                    self._breaker.record_success()
                    raise ConnectorError(
                        f"Requête LLM refusée ({response.status_code}).",
                        provider=self.provider,
                    )
                else:
                    self._breaker.record_success()
                    return self._parse(response.json(), input_tokens)

            if attempt < self._max_retries:
                self._sleep(delays[min(attempt, len(delays) - 1)])

        raise last_error or ConnectorError("Échec LLM inconnu.", provider=self.provider)

    def _parse(self, data: dict[str, Any], fallback_input_tokens: int) -> LLMCompletion:
        choices = data.get("choices") or []
        if not choices:
            raise ConnectorError("Réponse LLM sans contenu.", provider=self.provider)
        text = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", fallback_input_tokens))
        output_tokens = int(usage.get("completion_tokens", estimate_tokens(text)))
        self.budget.record(input_tokens + output_tokens)
        return LLMCompletion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_reference=data.get("model", self.model_reference),
            provider=self.provider,
        )

    def close(self) -> None:
        self._client.close()
