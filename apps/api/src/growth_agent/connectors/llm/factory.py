"""Sélection du fournisseur LLM. Toute configuration incomplète -> mock."""

from __future__ import annotations

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.llm.base import LLMProvider, TokenBudget
from growth_agent.connectors.llm.http import HTTPLLMProvider
from growth_agent.connectors.llm.mock import MockLLMProvider

_mock_singleton: MockLLMProvider | None = None


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    global _mock_singleton
    settings = settings or get_settings()
    budget = TokenBudget(daily_limit=settings.llm_daily_token_budget)

    if (
        settings.llm_provider == "http"
        and settings.llm_base_url
        and settings.llm_api_key
        and settings.llm_model
    ):
        return HTTPLLMProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            budget=budget,
            max_input_tokens=settings.llm_max_input_tokens,
        )

    if _mock_singleton is None:
        _mock_singleton = MockLLMProvider(budget=budget)
    return _mock_singleton


def reset_llm_singleton() -> None:
    global _mock_singleton
    _mock_singleton = None
