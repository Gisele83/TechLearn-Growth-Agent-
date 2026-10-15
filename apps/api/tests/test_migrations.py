"""Migrations : up, down, et correspondance avec les modèles."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from growth_agent.db.base import Base
from growth_agent.models import *  # noqa: F401,F403

API_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def database_url(tmp_path, monkeypatch) -> str:
    url = f"sqlite+pysqlite:///{tmp_path / 'migrations.sqlite3'}"
    # `migrations/env.py` relit la configuration : on la pointe sur la base
    # temporaire plutôt que sur celle du développeur.
    monkeypatch.setenv("GA_DATABASE_URL", url)
    from growth_agent.config import reset_settings_cache

    reset_settings_cache()
    yield url
    reset_settings_cache()


def test_upgrade_creates_every_table(database_url):
    command.upgrade(_alembic_config(database_url), "head")
    tables = set(inspect(create_engine(database_url)).get_table_names())
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert expected <= tables, f"tables manquantes : {expected - tables}"


def test_downgrade_removes_everything(database_url):
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert tables <= {"alembic_version"}


def test_upgrade_downgrade_upgrade_is_stable(database_url):
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    first = set(inspect(create_engine(database_url)).get_table_names())
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    assert set(inspect(create_engine(database_url)).get_table_names()) == first


def test_migration_matches_the_models(database_url):
    """Les colonnes créées par la migration correspondent aux modèles.

    Sans ce test, une colonne ajoutée à un modèle sans migration ne se
    verrait qu'en production.
    """
    command.upgrade(_alembic_config(database_url), "head")
    inspector = inspect(create_engine(database_url))
    for table_name, table in Base.metadata.tables.items():
        actual = {c["name"] for c in inspector.get_columns(table_name)}
        expected = {c.name for c in table.columns}
        assert expected == actual, f"écart sur « {table_name} » : {expected ^ actual}"


def test_unique_constraints_are_present(database_url):
    """Les contraintes qui portent les garanties métier — dédoublonnage,
    idempotence — doivent exister en base, pas seulement dans le code."""
    command.upgrade(_alembic_config(database_url), "head")
    inspector = inspect(create_engine(database_url))

    prospect_uniques = {
        tuple(sorted(c["column_names"]))
        for c in inspector.get_unique_constraints("prospect_candidates")
    }
    assert ("dedupe_key", "product_id") in prospect_uniques

    event_uniques = {
        tuple(sorted(c["column_names"]))
        for c in inspector.get_unique_constraints("outreach_events")
    }
    assert ("idempotency_key",) in event_uniques

    pilot_uniques = {
        tuple(sorted(c["column_names"]))
        for c in inspector.get_unique_constraints("product_pilot_events")
    }
    assert ("external_event_id", "product_id") in pilot_uniques
