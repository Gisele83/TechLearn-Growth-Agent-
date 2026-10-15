"""Dédoublonnage et normalisation."""

from __future__ import annotations

import pytest

from growth_agent.services.dedupe import (
    build_dedupe_key,
    email_domain,
    is_public_email_domain,
    normalize_domain,
    normalize_email,
    normalize_organization_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.exemple.example.org/a-propos", "exemple.example.org"),
        ("WWW.Exemple.Example.ORG", "exemple.example.org"),
        ("exemple.example.org", "exemple.example.org"),
        ("http://sous.exemple.example.org/", "sous.exemple.example.org"),
        ("", None),
        ("pas-un-domaine", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Marie.Dupont@Ecole.example.org ", "marie.dupont@ecole.example.org"),
        ("sans-arobase", None),
        ("@domaine.org", None),
        ("local@", None),
        ("a@b", None),
    ],
)
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_normalize_email_preserves_dots_and_tags():
    """On ne « canonicalise » pas l'adresse : chez beaucoup de serveurs,
    retirer un point ou un suffixe +tag désigne une autre boîte."""
    assert normalize_email("p.mbarga+devis@agro.example.org") == "p.mbarga+devis@agro.example.org"


def test_normalize_organization_name_strips_legal_forms_and_accents():
    assert normalize_organization_name("Établissements Côté & Fils SARL") == "cote fils"
    assert normalize_organization_name("AGRO DOUALA S.A.S.") == "agro douala"


def test_dedupe_key_identical_for_website_and_domain():
    from_website = build_dedupe_key(
        organization_name="Agro Douala", website="https://www.agro.example.org/contact"
    )
    from_domain = build_dedupe_key(organization_name="Agro Douala SARL", domain="agro.example.org")
    assert from_website == from_domain == "domain:agro.example.org"


def test_dedupe_key_uses_professional_email_domain_when_no_website():
    key = build_dedupe_key(
        organization_name="Lycée X", contact_email="direction@lycee-x.example.org"
    )
    assert key == "domain:lycee-x.example.org"


def test_dedupe_key_ignores_public_email_domain():
    """Une adresse Gmail ne dit rien de l'organisation : elle ne peut pas
    servir de clé, sinon deux entités sans site web entreraient en collision."""
    key = build_dedupe_key(
        organization_name="Collège Y", contact_email="directeur@gmail.com", country_code="CM"
    )
    assert key == "name:CM:college y"


def test_dedupe_key_falls_back_to_name_and_country():
    assert build_dedupe_key(organization_name="Collège Les Palmiers", country_code="cm") == (
        "name:CM:college les palmiers"
    )


def test_dedupe_key_distinguishes_same_name_in_two_countries():
    cm = build_dedupe_key(organization_name="Institut Central", country_code="CM")
    fr = build_dedupe_key(organization_name="Institut Central", country_code="FR")
    assert cm != fr


def test_dedupe_key_requires_something_usable():
    with pytest.raises(ValueError):
        build_dedupe_key(organization_name="   ")


def test_public_email_domain_detection():
    assert is_public_email_domain(email_domain("a@gmail.com")) is True
    assert is_public_email_domain(email_domain("a@entreprise.example.org")) is False
