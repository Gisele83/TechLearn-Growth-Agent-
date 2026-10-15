"""Liste d'opposition : la garantie la plus importante du système."""

from __future__ import annotations

import pytest

from growth_agent.models.enums import SuppressionReason, SuppressionScope
from growth_agent.services.suppression import add_suppression, check_suppression


def test_unknown_contact_is_allowed(session, product_educore):
    verdict = check_suppression(
        session, email="inconnu@exemple.example.org", product_id=product_educore.id
    )
    assert verdict.allowed is True


def test_global_suppression_blocks_both_products(session, product_educore, product_maintrix):
    add_suppression(session, value="refus@exemple.example.org", scope=SuppressionScope.GLOBAL)
    session.flush()
    for product in (product_educore, product_maintrix):
        verdict = check_suppression(
            session, email="refus@exemple.example.org", product_id=product.id
        )
        assert verdict.suppressed is True
        assert verdict.reason == SuppressionReason.OPT_OUT


def test_product_suppression_does_not_leak_to_other_product(
    session, product_educore, product_maintrix
):
    add_suppression(
        session,
        value="refus-educore@exemple.example.org",
        scope=SuppressionScope.PRODUCT,
        product_id=product_educore.id,
    )
    session.flush()
    assert check_suppression(
        session, email="refus-educore@exemple.example.org", product_id=product_educore.id
    ).suppressed is True
    assert check_suppression(
        session, email="refus-educore@exemple.example.org", product_id=product_maintrix.id
    ).allowed is True


def test_domain_suppression_blocks_every_address_of_that_domain(session, product_educore):
    add_suppression(session, value="groupe-ferme.example.org", scope=SuppressionScope.GLOBAL)
    session.flush()
    for address in ("a@groupe-ferme.example.org", "direction@groupe-ferme.example.org"):
        assert check_suppression(
            session, email=address, product_id=product_educore.id
        ).suppressed is True


def test_suppression_matching_is_case_insensitive(session, product_educore):
    add_suppression(session, value="Refus.Majuscule@Exemple.example.org")
    session.flush()
    assert check_suppression(
        session, email="REFUS.MAJUSCULE@EXEMPLE.EXAMPLE.ORG", product_id=product_educore.id
    ).suppressed is True


def test_missing_or_invalid_recipient_is_treated_as_blocking(session, product_educore):
    """Pas d'adresse exploitable = pas d'envoi. On n'écrit pas dans le vide."""
    for value in (None, "", "pas-une-adresse"):
        verdict = check_suppression(session, email=value, product_id=product_educore.id)
        assert verdict.suppressed is True
        assert verdict.reason == "invalid_recipient"


def test_adding_the_same_suppression_twice_is_idempotent(session):
    first = add_suppression(session, value="double@exemple.example.org")
    second = add_suppression(session, value="double@exemple.example.org")
    session.flush()
    assert first.id == second.id


def test_product_scope_requires_a_product(session):
    with pytest.raises(ValueError):
        add_suppression(session, value="x@exemple.example.org", scope=SuppressionScope.PRODUCT)
