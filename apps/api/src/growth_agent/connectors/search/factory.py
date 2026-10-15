"""Sélection du fournisseur de recherche. Configuration incomplète -> mock."""

from __future__ import annotations

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.search.base import SearchProvider
from growth_agent.connectors.search.http import HTTPSearchProvider
from growth_agent.connectors.search.mock import MockSearchProvider

_mock_singleton: MockSearchProvider | None = None


def get_search_provider(settings: Settings | None = None) -> SearchProvider:
    global _mock_singleton
    settings = settings or get_settings()
    if settings.search_provider == "http" and settings.search_base_url:
        return HTTPSearchProvider(
            base_url=settings.search_base_url, api_key=settings.search_api_key
        )
    if _mock_singleton is None:
        _mock_singleton = MockSearchProvider()
    return _mock_singleton


def reset_search_singleton() -> None:
    global _mock_singleton
    _mock_singleton = None
