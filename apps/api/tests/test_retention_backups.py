"""Interaction entre purge et sauvegardes.

Ces tests existent parce que le constat n'est pas intuitif : **une purge n'est
pas effective tant qu'une sauvegarde antérieure peut être restaurée**. La
restaurer réintroduit les données effacées.

Ce n'est pas un défaut qu'on corrige dans le code — c'est une propriété des
sauvegardes. Elle se traite par la politique de rétention des sauvegardes
(voir docs/07-politique-de-retention.md), et par le fait que la purge
planifiée rattrape automatiquement ce qu'une restauration a réintroduit.
"""

from __future__ import annotations

import shutil
from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from tests.test_retention import NOW, purge_settings

from growth_agent.db.base import Base
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.retention.service import PurgeService


def _file_backed_session(tmp_path, name: str):
    """Base sur fichier : une sauvegarde est une copie de fichier."""
    path = tmp_path / name
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    return path, engine, sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _seed_expired(session_factory):
    from growth_agent.seed.demo import seed_reference_data

    with session_factory() as session:
        products = seed_reference_data(session)
        session.add(
            ProspectCandidate(
                product_id=products["educore"].id,
                organization_name="Collège à purger",
                dedupe_key="domain:a-purger.example.org",
                domain="a-purger.example.org",
                country_code="CM",
                contact_full_name="Personne Test",
                contact_email="personne@a-purger.example.org",
                retention_until=NOW - timedelta(days=10),
            )
        )
        session.commit()


def test_restoring_a_pre_purge_backup_reintroduces_purged_data(tmp_path):
    """Le constat à connaître avant de promettre un effacement à quelqu'un."""
    db_path, engine, factory = _file_backed_session(tmp_path, "live.sqlite3")
    _seed_expired(factory)

    backup_path = tmp_path / "backup.sqlite3"
    shutil.copy(db_path, backup_path)

    with factory() as session:
        PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
        session.commit()

    with factory() as session:
        row = session.execute(select(ProspectCandidate)).scalars().one()
        assert row.contact_email is None, "la purge aurait dû effacer"

    engine.dispose()
    shutil.copy(backup_path, db_path)

    restored_engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    restored_factory = sessionmaker(bind=restored_engine, expire_on_commit=False, future=True)
    with restored_factory() as session:
        row = session.execute(select(ProspectCandidate)).scalars().one()
        # Voilà le point : la donnée est revenue.
        assert row.contact_email == "personne@a-purger.example.org"
    restored_engine.dispose()


def test_the_scheduled_purge_catches_up_after_a_restore(tmp_path):
    """L'atténuation : la purge étant idempotente et planifiée, la donnée
    réintroduite repart à l'exécution suivante."""
    db_path, engine, factory = _file_backed_session(tmp_path, "live2.sqlite3")
    _seed_expired(factory)

    backup_path = tmp_path / "backup2.sqlite3"
    shutil.copy(db_path, backup_path)

    with factory() as session:
        PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
        session.commit()

    engine.dispose()
    shutil.copy(backup_path, db_path)

    restored_engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    restored_factory = sessionmaker(bind=restored_engine, expire_on_commit=False, future=True)
    with restored_factory() as session:
        report = PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
        session.commit()
        assert report.counts["prospects_anonymized"] == 1

    with restored_factory() as session:
        row = session.execute(select(ProspectCandidate)).scalars().one()
        assert row.contact_email is None
    restored_engine.dispose()
