"""Sélection de l'adaptateur CRM selon la configuration.

Toute configuration incomplète retombe sur le mock. On ne tente jamais un appel
réseau « au cas où » : un connecteur mal configuré doit être inoffensif.
"""

from __future__ import annotations

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.crm.base import CRMAdapter
from growth_agent.connectors.crm.hubspot import HubSpotCRMAdapter
from growth_agent.connectors.crm.mock import MockCRMAdapter

_mock_singleton: MockCRMAdapter | None = None


def get_crm_adapter(settings: Settings | None = None) -> CRMAdapter:
    global _mock_singleton
    settings = settings or get_settings()

    if settings.crm_provider == "hubspot" and settings.crm_api_token:
        return HubSpotCRMAdapter(
            base_url=settings.crm_base_url,
            api_token=settings.crm_api_token,
            max_retries=settings.crm_max_retries,
            rate_limit_per_second=settings.crm_rate_limit_per_second,
            search_fallback=settings.crm_search_fallback,
        )

    # Le mock est partagé pour que l'état survive entre deux requêtes HTTP en
    # développement local (sinon chaque requête repartirait d'un CRM vide).
    if _mock_singleton is None:
        _mock_singleton = MockCRMAdapter()
    return _mock_singleton


def reset_crm_singleton() -> None:
    global _mock_singleton
    _mock_singleton = None
