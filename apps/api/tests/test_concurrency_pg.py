"""Concurrence réelle, sur un vrai PostgreSQL.

SQLite sérialise les écrivains : il ne peut pas démontrer le défaut qu'on
cherche à exclure ici — deux processus qui franchissent ensemble un contrôle
anti-doublon lisant avant d'écrire, et expédient deux fois le même message.
Ces tests exigent donc un PostgreSQL, et sont ignorés sans lui.

Lancement :

    GA_TEST_PG_URL="postgresql+psycopg://user@/base?host=/chemin/socket" \
        pytest tests/test_concurrency_pg.py -v

Aucun email ne part : le fournisseur est une doublure qui compte les appels.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from growth_agent.config import Settings
from growth_agent.connectors.email.base import EmailMessage, EmailProvider, EmailSendResult
from growth_agent.db.base import Base
from growth_agent.models.catalog import Campaign, Product
from growth_agent.models.enums import (
    ApprovalSubject,
    DeliveryState,
    DraftStatus,
    OutreachEventType,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent, UnsubscribeToken
from growth_agent.models.prospect import ProspectCandidate
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.outreach import send_draft

PG_URL = os.environ.get("GA_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="GA_TEST_PG_URL non défini : test de concurrence ignoré"
)

#: Nombre de travailleurs lancés simultanément sur le même brouillon.
WORKERS = 12


class CountingProvider(EmailProvider):
    """Doublure de fournisseur. Elle n'envoie rien ; elle compte, et elle
    enregistre l'ordre des appels pour que le test puisse les examiner."""

    provider = "smtp"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        with self._lock:
            self.calls.append(idempotency_key)
        return EmailSendResult(
            accepted=True,
            provider=self.provider,
            message_id=f"smtp-{uuid.uuid4().hex[:12]}",
            sent_at=datetime.now(UTC),
        )


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(PG_URL, future=True, pool_size=WORKERS + 4, max_overflow=8)
    # Base repartie de zéro : aucune donnée réelle, uniquement du synthétique.
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def pg_factory(pg_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=pg_engine, expire_on_commit=False, future=True)


@pytest.fixture
def real_settings() -> Settings:
    return Settings(
        email_provider="smtp",
        email_sending_enabled=True,
        email_from="contact@exemple.example.org",
        public_base_url="https://go.exemple.example.org",
    )


def _clean(session: Session) -> None:
    for model in (OutreachEvent, UnsubscribeToken, MessageDraft, ProspectCandidate):
        session.query(model).delete()
    session.commit()


@pytest.fixture
def scenario(pg_factory):
    """Un produit, une campagne, un prospect, un brouillon approuvé.
    Données entièrement synthétiques, domaines réservés par la RFC 2606."""
    session = pg_factory()
    try:
        product = session.execute(
            select(Product).where(Product.code == "educore")
        ).scalar_one_or_none()
        if product is None:
            product = Product(
                code="educore", name="EduCore", sector="Éducation", description="Test"
            )
            session.add(product)
            session.flush()
        campaign = session.execute(
            select(Campaign).where(Campaign.product_id == product.id)
        ).scalar_one_or_none()
        if campaign is None:
            campaign = Campaign(
                product_id=product.id, slug="test", name="Campagne de test", legal_regime="CM"
            )
            session.add(campaign)
            session.flush()
        _clean(session)

        prospect = ProspectCandidate(
            product_id=product.id,
            campaign_id=campaign.id,
            organization_name="Lycée de test",
            dedupe_key=f"domain:conc-{uuid.uuid4().hex[:8]}.example.org",
            domain="concurrence.example.org",
            country_code="CM",
            sector="Enseignement secondaire",
            contact_full_name="Contact Test",
            contact_role="Directeur",
            contact_email="concurrence@exemple.example.org",
        )
        session.add(prospect)
        session.flush()

        draft = MessageDraft(
            product_id=product.id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            recipient_email=prospect.contact_email,
            subject="Objet unique",
            body="Bonjour. Répondez STOP pour ne plus recevoir nos messages.",
        )
        session.add(draft)
        session.flush()
        approval = request_approval(
            session,
            subject_type=ApprovalSubject.MESSAGE,
            subject_id=draft.id,
            requested_by="auteur@exemple.example.org",
            summary="Message",
            draft=draft,
        )
        decide_approval(
            session,
            approval_id=approval.id,
            decided_by="valideur@exemple.example.org",
            approve=True,
        )
        session.commit()
        return {"product_id": product.id, "draft_id": draft.id, "prospect_id": prospect.id}
    finally:
        session.close()


def _race(pg_factory, draft_id, provider, settings, workers: int = WORKERS) -> list[dict]:
    """Lance `workers` travailleurs qui tentent le même envoi au même instant.

    La barrière est ce qui rend la course réelle : sans elle, le premier
    travailleur aurait le temps de finir avant que le dernier ne démarre, et
    le test ne prouverait rien.
    """
    barrier = threading.Barrier(workers)
    results: list[dict] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        session = pg_factory()
        try:
            draft = session.get(MessageDraft, draft_id)
            barrier.wait(timeout=30)
            outcome = send_draft(
                session,
                draft=draft,
                email_provider=provider,
                actor=f"worker-{index}@exemple.example.org",
                settings=settings,
            )
            session.commit()
            record = {
                "worker": index,
                "sent": outcome.sent,
                "reason": outcome.reason_code,
                "state": outcome.delivery_state,
            }
        except Exception as exc:  # noqa: BLE001 - on veut voir l'échec, pas le masquer
            session.rollback()
            record = {"worker": index, "sent": False, "reason": f"exception:{type(exc).__name__}"}
        finally:
            session.close()
        with lock:
            results.append(record)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not t.is_alive() for t in threads), "un travailleur ne s'est pas terminé"
    return results


def test_twelve_concurrent_workers_send_exactly_one_message(
    pg_factory, scenario, real_settings, capsys
):
    provider = CountingProvider()
    results = _race(pg_factory, scenario["draft_id"], provider, real_settings)

    winners = [r for r in results if r["sent"]]
    losers = [r for r in results if not r["sent"]]

    session = pg_factory()
    try:
        events = list(
            session.execute(
                select(OutreachEvent).where(
                    OutreachEvent.draft_id == scenario["draft_id"],
                    OutreachEvent.event_type == OutreachEventType.SENT,
                )
            ).scalars()
        )
        draft = session.get(MessageDraft, scenario["draft_id"])
        blocked = session.execute(
            select(func.count())
            .select_from(OutreachEvent)
            .where(
                OutreachEvent.draft_id == scenario["draft_id"],
                OutreachEvent.event_type == OutreachEventType.SEND_BLOCKED,
            )
        ).scalar_one()
        draft_status = draft.status
    finally:
        session.close()

    # Trace lisible, reproductible : c'est la preuve demandée.
    print(
        f"\n--- concurrence PostgreSQL ---\n"
        f"travailleurs lancés        : {len(results)}\n"
        f"réservations réussies      : {len(winners)}\n"
        f"travailleurs refusés       : {len(losers)}\n"
        f"motifs de refus            : {sorted({r['reason'] for r in losers})}\n"
        f"appels au fournisseur      : {len(provider.calls)}\n"
        f"événements « sent » en base: {len(events)}\n"
        f"états en base              : {[e.delivery_state for e in events]}\n"
        f"événements « send_blocked »: {blocked}\n"
        f"statut final du brouillon  : {draft_status}\n"
    )

    assert len(results) == WORKERS
    assert len(winners) == 1, f"{len(winners)} travailleurs ont cru envoyer"
    assert len(provider.calls) == 1, "le fournisseur a été appelé plus d'une fois"
    assert len(events) == 1
    assert events[0].delivery_state == DeliveryState.SENT
    assert events[0].is_simulated is False
    assert draft_status == DraftStatus.SENT
    # Chaque refus est motivé et observable : aucun échec silencieux.
    assert {r["reason"] for r in losers} == {"duplicate"}
    assert blocked == WORKERS - 1


def test_unique_constraint_is_what_enforces_it(pg_factory):
    """La course n'est gagnée que par la contrainte d'unicité. On l'interroge
    directement plutôt que de la supposer : un index perdu lors d'une
    migration future ferait repasser ce test au rouge."""
    session = pg_factory()
    try:
        indexes = (
            session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'outreach_events' "
                    "AND indexdef ILIKE '%%UNIQUE%%' "
                    "AND indexdef ILIKE '%%idempotency_key%%'"
                )
            )
            .scalars()
            .all()
        )
    finally:
        session.close()
    assert indexes, "aucune contrainte d'unicité sur outreach_events.idempotency_key"


def test_state_survives_a_restart(pg_factory, scenario, real_settings):
    """Après la course, on ferme tout et on repart d'un moteur neuf : l'état
    doit être le même, et une nouvelle tentative doit rester refusée."""
    provider = CountingProvider()
    _race(pg_factory, scenario["draft_id"], provider, real_settings, workers=6)
    assert len(provider.calls) == 1

    # « Redémarrage » : nouveau moteur, nouveau pool, rien en mémoire.
    engine = create_engine(PG_URL, future=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        draft = session.get(MessageDraft, scenario["draft_id"])
        assert draft.status == DraftStatus.SENT
        events = list(
            session.execute(
                select(OutreachEvent).where(
                    OutreachEvent.draft_id == draft.id,
                    OutreachEvent.event_type == OutreachEventType.SENT,
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].delivery_state == DeliveryState.SENT

        after_restart = CountingProvider()
        outcome = send_draft(
            session,
            draft=draft,
            email_provider=after_restart,
            actor="reprise@exemple.example.org",
            settings=real_settings,
        )
        session.commit()
        assert outcome.sent is False
        assert outcome.reason_code == "already_sent"
        assert after_restart.calls == []
    finally:
        session.close()
        engine.dispose()


def test_concurrent_unsubscribes_do_not_duplicate_the_suppression(
    pg_factory, scenario, real_settings
):
    """Un clic multiplié par un préchargement antivirus ne doit pas créer
    plusieurs oppositions pour la même adresse."""
    from growth_agent.models.outreach import SuppressionEntry
    from growth_agent.services.unsubscribe import issue_token, process_unsubscribe

    session = pg_factory()
    try:
        issued = issue_token(
            session,
            email="desinscription@exemple.example.org",
            product_id=scenario["product_id"],
        )
        session.commit()
        raw = issued.raw_token
    finally:
        session.close()

    barrier = threading.Barrier(8)
    outcomes: list[bool] = []
    lock = threading.Lock()

    def click() -> None:
        s = pg_factory()
        try:
            barrier.wait(timeout=30)
            result = process_unsubscribe(s, raw_token=raw)
            s.commit()
            accepted = result.accepted
        except Exception:  # noqa: BLE001
            s.rollback()
            accepted = False
        finally:
            s.close()
        with lock:
            outcomes.append(accepted)

    threads = [threading.Thread(target=click) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    session = pg_factory()
    try:
        entries = (
            session.query(SuppressionEntry)
            .filter(SuppressionEntry.normalized_value == "desinscription@exemple.example.org")
            .count()
        )
    finally:
        session.close()

    # Au moins un clic aboutit, et la base ne contient qu'une opposition.
    assert any(outcomes)
    assert entries == 1
