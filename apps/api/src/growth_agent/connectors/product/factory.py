"""Sélection de l'adaptateur produit.

Règle unique et non contournable : sans autorisation explicite dans la
configuration, on renvoie le mock. Jamais d'appel réel « par défaut ».
"""

from __future__ import annotations

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.product.base import ProductAdapter
from growth_agent.connectors.product.http import HTTPProductAdapter
from growth_agent.connectors.product.mock import MockEduCoreAdapter, MockMaintrixAdapter

_mocks: dict[str, ProductAdapter] = {}


def get_product_adapter(product_code: str, settings: Settings | None = None) -> ProductAdapter:
    settings = settings or get_settings()
    code = product_code.lower()

    if settings.product_adapter_active(code):
        token = getattr(settings, f"{code}_api_token", "")
        base_url = getattr(settings, f"{code}_base_url", "")
        return HTTPProductAdapter(
            product_code=code,
            base_url=base_url,
            api_token=token,
            enabled=True,
        )

    if code not in _mocks:
        if code == "maintrix":
            _mocks[code] = MockMaintrixAdapter()
        elif code == "educore":
            _mocks[code] = MockEduCoreAdapter()
        else:
            raise ValueError(f"Produit inconnu : {product_code}")
    return _mocks[code]


def reset_product_singletons() -> None:
    _mocks.clear()
