"""Fixtures de test.

Chaque test reçoit une base SQLite neuve sur disque temporaire. On n'utilise
pas `:memory:` : les tests passant par le client HTTP ouvrent plusieurs
connexions, et une base en mémoire ne serait pas partagée entre elles.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("GA_ENVIRONMENT", "local")
os.environ.setdefault("GA_EMAIL_SENDING_ENABLED", "false")
# Un expéditeur est requis pour rédiger : sans lui le message serait anonyme.
os.environ.setdefault("GA_SENDER_NAME", "Awa Njoya")
os.environ.setdefault("GA_SENDER_ROLE", "Chargée de développement")
os.environ.setdefault("GA_SENDER_ORGANIZATION", "TechLearn")

from growth_agent.api.deps import db_session  # noqa: E402
from growth_agent.config import get_settings  # noqa: E402
from growth_agent.connectors.crm.mock import MockCRMAdapter  # noqa: E402
from growth_agent.connectors.email.mock import MockEmailProvider  # noqa: E402
from growth_agent.connectors.llm.mock import MockLLMProvider  # noqa: E402
from growth_agent.connectors.product.mock import (  # noqa: E402
    MockEduCoreAdapter,
    MockMaintrixAdapter,
)
from growth_agent.connectors.search.mock import MockSearchProvider  # noqa: E402
from growth_agent.db.base import Base  # noqa: E402
from growth_agent.main import create_app  # noqa: E402
from growth_agent.models.catalog import Campaign, IdealCustomerProfile, Product  # noqa: E402
from growth_agent.models.prospect import ProspectCandidate, ProspectSource  # noqa: E402
from growth_agent.seed.demo import seed_demo_data  # noqa: E402


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}"
    eng = create_engine(url, future=True, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def seeded(session) -> dict[str, object]:
    """Produits, profils cibles, campagnes, gabarits et comptes de démonstration."""
    seed_demo_data(session)
    session.commit()
    return {p.code: p for p in session.query(Product).all()}


@pytest.fixture
def product_educore(seeded) -> Product:
    return seeded["educore"]


@pytest.fixture
def product_maintrix(seeded) -> Product:
    return seeded["maintrix"]


@pytest.fixture
def icp_educore(session, product_educore) -> IdealCustomerProfile:
    return (
        session.query(IdealCustomerProfile)
        .filter(IdealCustomerProfile.product_id == product_educore.id)
        .one()
    )


@pytest.fixture
def campaign_educore(session, product_educore) -> Campaign:
    return session.query(Campaign).filter(Campaign.product_id == product_educore.id).one()


@pytest.fixture
def ctx(session):
    """Contexte d'exécution d'un agent."""
    from growth_agent.agents.base import AgentContext

    return AgentContext(
        session=session, actor="testeur@exemple.example.org", actor_role="commercial"
    )


@pytest.fixture
def crm() -> MockCRMAdapter:
    return MockCRMAdapter()


@pytest.fixture
def llm() -> MockLLMProvider:
    return MockLLMProvider()


@pytest.fixture
def search() -> MockSearchProvider:
    return MockSearchProvider()


@pytest.fixture
def email() -> MockEmailProvider:
    return MockEmailProvider()


@pytest.fixture
def maintrix_adapter() -> MockMaintrixAdapter:
    return MockMaintrixAdapter()


@pytest.fixture
def educore_adapter() -> MockEduCoreAdapter:
    return MockEduCoreAdapter()


@pytest.fixture
def make_prospect(session):
    """Fabrique un prospect avec ses sources, pour éviter la répétition."""

    def _make(
        *,
        product: Product,
        organization_name: str = "Organisation de test",
        contact_email: str | None = "contact@test.example.org",
        contact_role: str | None = "Directeur administratif",
        domain: str | None = "test.example.org",
        sector: str | None = "Enseignement secondaire",
        headcount: int | None = 80,
        with_source: bool = True,
        excerpt: str = "L'établissement souhaite améliorer sa gestion administrative.",
        campaign: Campaign | None = None,
    ) -> ProspectCandidate:
        prospect = ProspectCandidate(
            product_id=product.id,
            campaign_id=campaign.id if campaign else None,
            organization_name=organization_name,
            dedupe_key=f"domain:{domain}" if domain else f"name:XX:{uuid.uuid4().hex[:8]}",
            website=f"https://{domain}" if domain else None,
            domain=domain,
            country_code="CM",
            sector=sector,
            headcount_estimate=headcount,
            contact_full_name="Contact Test",
            contact_role=contact_role,
            contact_email=contact_email,
        )
        session.add(prospect)
        session.flush()
        if with_source:
            session.add(
                ProspectSource(
                    prospect_id=prospect.id,
                    kind="public_website",
                    url=f"https://{domain or 'source.example.org'}/a-propos",
                    title="À propos",
                    excerpt=excerpt,
                    collected_at=datetime.now(UTC),
                    provider="mock",
                )
            )
            session.flush()
        return prospect

    return _make


@pytest.fixture
def client(session_factory, email, llm, search) -> Iterator[TestClient]:
    """Client HTTP branché sur la base de test et des connecteurs maîtrisés."""
    from growth_agent.api.deps import email_provider, llm_provider, search_provider

    app = create_app()

    def _override_session() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[db_session] = _override_session
    app.dependency_overrides[email_provider] = lambda: email
    app.dependency_overrides[llm_provider] = lambda: llm
    app.dependency_overrides[search_provider] = lambda: search

    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth(client):
    """Fabrique des en-têtes d'authentification pour un rôle de démonstration."""
    accounts = {
        "admin": ("admin@growth.example.org", "DemoAdmin!2026"),
        "commercial": ("commercial@growth.example.org", "DemoCommercial!2026"),
        "approbateur": ("approbateur@growth.example.org", "DemoApprobateur!2026"),
        "lecteur": ("lecteur@growth.example.org", "DemoLecteur!2026"),
    }

    def _auth(role: str) -> dict[str, str]:
        email_address, password = accounts[role]
        response = client.post(
            "/api/v1/auth/login", json={"email": email_address, "password": password}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _auth
