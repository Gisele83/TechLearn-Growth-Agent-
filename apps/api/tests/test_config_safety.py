"""Configuration : les garde-fous qui empêchent une mise en production bancale."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from growth_agent.config import Environment, Settings


def test_local_defaults_are_entirely_offline():
    settings = Settings()
    assert settings.crm_provider == "mock"
    assert settings.llm_provider == "mock"
    assert settings.search_provider == "mock"
    assert settings.can_send_email() is False
    assert settings.product_adapter_active("maintrix") is False
    assert settings.product_adapter_active("educore") is False


def test_production_requires_a_strong_secret_key():
    with pytest.raises(ValidationError, match="GA_SECRET_KEY"):
        Settings(environment=Environment.PRODUCTION, debug=False, secret_key="court")


def test_production_refuses_debug_mode():
    with pytest.raises(ValidationError, match="GA_DEBUG"):
        Settings(environment=Environment.PRODUCTION, debug=True, secret_key="k" * 40)


def test_production_refuses_a_wildcard_cors_origin():
    with pytest.raises(ValidationError, match="GA_CORS_ORIGINS"):
        Settings(
            environment=Environment.PRODUCTION,
            debug=False,
            secret_key="k" * 40,
            cors_origins="*",
        )


def test_a_valid_production_configuration_is_accepted():
    settings = Settings(
        environment=Environment.PRODUCTION,
        debug=False,
        secret_key="k" * 40,
        cors_origins="https://growth.example.org",
    )
    assert settings.is_production is True
    assert settings.cors_origin_list == ["https://growth.example.org"]


@pytest.mark.parametrize("product", ["maintrix", "educore"])
def test_product_connector_needs_all_three_conditions(product):
    """Flag ET adaptateur http ET URL : deux sur trois ne suffisent pas."""
    assert Settings(**{f"{product}_enabled": True}).product_adapter_active(product) is False
    assert (
        Settings(
            **{f"{product}_enabled": True, f"{product}_adapter": "http"}
        ).product_adapter_active(product)
        is False
    )
    assert (
        Settings(
            **{
                f"{product}_enabled": True,
                f"{product}_adapter": "http",
                f"{product}_base_url": "https://api.exemple.example.org",
            }
        ).product_adapter_active(product)
        is True
    )


def test_enabling_the_flag_alone_warns_rather_than_acting():
    warnings = Settings(maintrix_enabled=True).configuration_warnings()
    assert any("maintrix" in w for w in warnings)


def test_sending_flag_without_real_provider_is_reported():
    warnings = Settings(email_sending_enabled=True, email_provider="mock").configuration_warnings()
    assert any("aucun envoi réel" in w for w in warnings)


def test_real_crm_without_token_falls_back_to_mock():
    from growth_agent.connectors.crm.factory import get_crm_adapter, reset_crm_singleton

    reset_crm_singleton()
    adapter = get_crm_adapter(Settings(crm_provider="hubspot", crm_api_token=""))
    assert adapter.provider == "mock"
    reset_crm_singleton()


def test_real_llm_without_key_falls_back_to_mock():
    from growth_agent.connectors.llm.factory import get_llm_provider, reset_llm_singleton

    reset_llm_singleton()
    provider = get_llm_provider(Settings(llm_provider="http", llm_base_url="https://x.example.org"))
    assert provider.provider == "mock"
    reset_llm_singleton()


def test_email_factory_never_returns_a_real_sender_by_default():
    from growth_agent.connectors.email.factory import get_email_provider, reset_email_singleton

    reset_email_singleton()
    assert get_email_provider(Settings()).provider == "mock"
    reset_email_singleton()


def test_product_factory_returns_mock_without_authorization():
    from growth_agent.connectors.product.factory import (
        get_product_adapter,
        reset_product_singletons,
    )

    reset_product_singletons()
    for product in ("maintrix", "educore"):
        adapter = get_product_adapter(product, Settings())
        assert adapter.mode == "mock"
        assert adapter.is_read_only is True
    reset_product_singletons()


def test_disabled_http_product_adapter_refuses_to_initialize():
    """Le connecteur réel ne peut pas être instancié sans autorisation :
    aucun paquet ne part vers Maintrix ou EduCore par accident."""
    from growth_agent.connectors.base import ConnectorDisabledError
    from growth_agent.connectors.product.http import HTTPProductAdapter

    with pytest.raises(ConnectorDisabledError, match="autorisation"):
        HTTPProductAdapter(
            product_code="maintrix",
            base_url="https://maintrix.exemple.example.org",
            api_token="jeton",
            enabled=False,
        )


def test_smtp_provider_refuses_to_initialize_when_sending_is_off():
    from growth_agent.connectors.email.base import EmailSendingDisabled
    from growth_agent.connectors.email.smtp import SMTPEmailProvider

    with pytest.raises(EmailSendingDisabled):
        SMTPEmailProvider(
            host="smtp.exemple.example.org",
            port=587,
            username="u",
            password="p",
            from_email="a@exemple.example.org",
            sending_enabled=False,
        )
