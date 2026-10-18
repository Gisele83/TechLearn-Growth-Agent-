"""Ce qui reste après la purge doit cesser de désigner quelqu'un.

Effacer `contact_full_name` et `contact_email` ne suffit pas. Autour d'eux
subsistent des champs qui ne nomment personne et y ramènent tout de même :
un titre de page web (« Marie Dupont, directrice »), un chemin d'URL
(`/equipe/marie-dupont`), une évaluation qui cite la phrase où elle est
nommée, une clé libre dans le `payload` d'un événement, l'identifiant de sa
fiche dans le CRM.

Le test central ici ne vérifie pas une liste de champs : il **balaie toute
la base** à la recherche du nom et de l'adresse, et n'accepte leur survie
que là où une raison écrite le justifie. Un champ ajouté demain et oublié
par la purge y tombera tout seul — c'est le seul garde-fou qui résiste à
l'ajout de colonnes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text

from growth_agent.config import Settings
from growth_agent.models.enums import OutreachEventType
from growth_agent.models.outreach import MessageDraft, OutreachEvent, UnsubscribeToken
from growth_agent.models.prospect import CRMMapping, ProspectSource, QualificationAssessment
from growth_agent.retention.policy import (
    KEPT_CRITERION_KEYS,
    KEPT_EVENT_PAYLOAD_KEYS,
    strip_url_path,
)
from growth_agent.retention.service import PurgeService
from growth_agent.services.outreach import record_outreach_event

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

#: Jetons choisis pour être introuvables ailleurs : si l'un d'eux réapparaît
#: après la purge, c'est qu'il vient bien de ce prospect-ci.
NOM = "Zephyrine-Kalombo"
MAIL = "zephyrine.kalombo@echu-probe.example.org"
DOMAINE = "echu-probe.example.org"

#: Là où l'adresse ou le domaine survivent **volontairement**, et pourquoi.
#:
#: Cette liste n'est pas une tolérance : c'est la partie motivée de la
#: politique de rétention. Y ajouter une ligne demande d'écrire la raison,
#: ce qui est exactement la discussion qu'on veut provoquer.
SURVIVANCES_MOTIVEES: dict[tuple[str, str], str] = {
    ("prospect_candidates", "domain"): (
        "Identifiant d'organisation, pas de personne. Le retirer ferait "
        "disparaître l'organisation des indicateurs passés."
    ),
    ("prospect_candidates", "website"): (
        "Adresse publique de l'organisation, construite sur le domaine. Elle "
        "ne porte pas de chemin et ne désigne donc aucune personne."
    ),
    ("prospect_candidates", "dedupe_key"): (
        "Dérivée du domaine. Son effacement rouvrirait la porte aux doublons."
    ),
    ("prospect_sources", "url"): (
        "Réduite à son origine par `strip_url_path` : elle prouve d'où venait "
        "le prospect, sans plus porter le chemin qui le nommait."
    ),
    ("unsubscribe_tokens", "email"): (
        "Conservée pour que l'opposition reste enregistrable si la personne "
        "clique après la purge. Une opposition prime sur l'effacement — "
        "arbitrage ouvert : borner cette conservation dans le temps."
    ),
    ("suppression_entries", "normalized_value"): (
        "L'adresse d'une personne qui a demandé à ne plus être contactée. "
        "L'effacer reviendrait à la recontacter."
    ),
}


def purge_settings(**overrides) -> Settings:
    base = {
        "purge_enabled": True,
        "data_retention_days": 365,
        "draft_retention_days": 180,
        "audit_retention_days": 1095,
        "pilot_event_retention_days": 1825,
        "purge_max_records_per_run": 5000,
        "purge_max_table_ratio": 1.0,
        "purge_batch_size": 50,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def prospect_bavard(session, product_educore, make_prospect):
    """Un prospect échu dont le nom a été recopié partout où c'est possible.

    Les tests de purge existants travaillent sur un prospect sobre, et
    passaient donc à côté des champs qui ne sont renseignés que dans la vraie
    vie : le titre d'une page, le raisonnement d'une évaluation, une clé
    inventée dans un `payload`.
    """
    prospect = make_prospect(
        product=product_educore,
        organization_name="Collège Échu",
        contact_email=MAIL,
        contact_role="Directrice",
        domain=DOMAINE,
        excerpt=f"Entretien avec {NOM}, directrice de l'établissement.",
    )
    prospect.contact_full_name = NOM
    prospect.notes = f"Rencontre avec {NOM} le 3 mars."
    prospect.retention_until = NOW - timedelta(days=30)

    for source in prospect.sources:
        source.url = f"https://{DOMAINE}/equipe/{NOM}"
        source.title = f"{NOM}, directrice"

    session.add(
        MessageDraft(
            product_id=product_educore.id,
            prospect_id=prospect.id,
            recipient_email=MAIL,
            subject=f"Bonjour {NOM}",
            body=f"Bonjour {NOM}, voici notre proposition.",
            variables_used={"contact_name": NOM},
            blocked_reason=f"Contact {MAIL} opposé à la prospection.",
        )
    )
    session.add(
        QualificationAssessment(
            prospect_id=prospect.id,
            total_score=70,
            max_score=100,
            criteria=[
                {
                    "key": "decision_maker",
                    "label": "Interlocuteur décisionnaire",
                    "points": 20,
                    "max_points": 20,
                    "status": "verified",
                    "evidence": [f"{NOM} est la directrice"],
                    "comment": f"Confirmé par {NOM}.",
                }
            ],
            rationale=f"{NOM} a confirmé le besoin.",
            unknowns=[f"Budget de {NOM} inconnu"],
        )
    )
    session.add(
        CRMMapping(
            crm_provider="hubspot",
            object_type="contact",
            local_id=prospect.id,
            remote_id="CRM-12345",
            idempotency_key=f"contact:{prospect.id}",
        )
    )
    session.add(
        UnsubscribeToken(
            prospect_id=prospect.id,
            product_id=product_educore.id,
            token_hash="0" * 64,
            email=MAIL,
        )
    )
    record_outreach_event(
        session,
        product_id=product_educore.id,
        prospect_id=prospect.id,
        event_type=OutreachEventType.SEND_BLOCKED,
        occurred_at=NOW - timedelta(days=200),
        idempotency_key=f"blocked:{prospect.id}",
        payload={
            "reason_code": "suppressed",
            "detail": f"Contact {MAIL} opposé.",
            "contact": NOM,
            "message_id": "sg-abcdef",
        },
    )
    session.flush()
    return prospect


def _purger(session):
    PurgeService(session, settings=purge_settings()).apply(dry_run=False, now=NOW)
    session.flush()


# --- Le balayage ------------------------------------------------------------


def test_after_the_purge_nothing_in_the_database_still_names_the_person(session, prospect_bavard):
    """Le test qui attrape les colonnes qu'on n'a pas pensé à regarder."""
    _purger(session)

    inspecteur = inspect(session.bind)
    restes: list[str] = []
    for table in inspecteur.get_table_names():
        colonnes = [colonne["name"] for colonne in inspecteur.get_columns(table)]
        # Le nom de table vient de l'inspecteur SQLAlchemy, jamais d'une
        # saisie : c'est le seul moyen de balayer des tables qu'on ne connaît
        # pas d'avance, ce qui est tout l'intérêt du test.
        for ligne in session.execute(text(f'SELECT * FROM "{table}"')).all():  # noqa: S608
            for colonne, valeur in zip(colonnes, ligne, strict=False):
                if valeur is None:
                    continue
                texte = str(valeur)
                if NOM not in texte and MAIL not in texte and DOMAINE not in texte:
                    continue
                if (table, colonne) in SURVIVANCES_MOTIVEES:
                    continue
                restes.append(f"{table}.{colonne} = {texte[:120]}")

    assert not restes, (
        "Donnée réidentifiante survivant à la purge sans justification :\n  "
        + "\n  ".join(sorted(set(restes)))
    )


def test_the_persons_name_survives_nowhere_at_all(session, prospect_bavard):
    """Le domaine est une organisation ; le nom ne l'est jamais.

    Aucune survivance n'est motivée pour le nom : séparer les deux
    vérifications empêche qu'une justification écrite pour le domaine couvre
    un jour le nom par ricochet.
    """
    _purger(session)

    inspecteur = inspect(session.bind)
    for table in inspecteur.get_table_names():
        colonnes = [colonne["name"] for colonne in inspecteur.get_columns(table)]
        # Le nom de table vient de l'inspecteur SQLAlchemy, jamais d'une
        # saisie : c'est le seul moyen de balayer des tables qu'on ne connaît
        # pas d'avance, ce qui est tout l'intérêt du test.
        for ligne in session.execute(text(f'SELECT * FROM "{table}"')).all():  # noqa: S608
            for colonne, valeur in zip(colonnes, ligne, strict=False):
                assert NOM not in str(valeur), f"nom retrouvé dans {table}.{colonne}"


def test_every_tolerated_survival_states_a_reason(session):
    """Une exception sans motif écrit est une exception qu'on n'a pas pesée."""
    for (table, colonne), motif in SURVIVANCES_MOTIVEES.items():
        assert len(motif) > 50, f"{table}.{colonne} toléré sans justification suffisante"


# --- Champ par champ --------------------------------------------------------


def test_a_source_keeps_its_origin_and_loses_its_path(session, prospect_bavard):
    """Un chemin dit souvent qui ; une origine dit seulement où."""
    _purger(session)
    source = session.execute(
        select(ProspectSource).where(ProspectSource.prospect_id == prospect_bavard.id)
    ).scalar_one()
    assert source.url == f"https://{DOMAINE}"
    assert source.title == "[anonymisé]"
    assert source.excerpt == "[anonymisé]"
    # La provenance reste démontrable : c'est elle qui justifie a posteriori
    # pourquoi ce prospect avait été retenu.
    assert source.collected_at is not None
    assert source.kind


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        ("https://site.example.org/equipe/marie-dupont", "https://site.example.org"),
        ("https://site.example.org/a?nom=marie#bio", "https://site.example.org"),
        ("https://site.example.org", "https://site.example.org"),
        ("", ""),
        ("marie-dupont", "[anonymisé]"),
    ],
)
def test_url_reduction_keeps_the_origin_only(url, attendu):
    assert strip_url_path(url) == attendu


def test_an_assessment_keeps_its_score_and_loses_its_quotes(session, prospect_bavard):
    """Le score doit rester recalculable : un score qu'on ne peut plus
    recalculer n'est plus contestable, et la personne évaluée y perd."""
    _purger(session)
    evaluation = session.execute(
        select(QualificationAssessment).where(
            QualificationAssessment.prospect_id == prospect_bavard.id
        )
    ).scalar_one()
    assert evaluation.total_score == 70
    assert evaluation.max_score == 100
    assert evaluation.rationale == "[anonymisé]"
    assert evaluation.unknowns == []
    critere = evaluation.criteria[0]
    assert critere["points"] == 20
    assert critere["status"] == "verified"
    assert set(critere) <= KEPT_CRITERION_KEYS
    assert "evidence" not in critere and "comment" not in critere


def test_an_event_payload_keeps_only_what_an_indicator_reads(session, prospect_bavard):
    """Liste blanche : une clé inventée par un appelant futur tombe d'elle-même."""
    _purger(session)
    evenement = session.execute(
        select(OutreachEvent).where(OutreachEvent.prospect_id == prospect_bavard.id)
    ).scalar_one()
    # `reason_code` alimente « envois bloqués par motif » : le perdre
    # effacerait un indicateur sans rien protéger.
    assert evenement.payload["reason_code"] == "suppressed"
    assert set(evenement.payload) <= KEPT_EVENT_PAYLOAD_KEYS
    assert "detail" not in evenement.payload
    assert "contact" not in evenement.payload
    # Identifiant chez le fournisseur d'envoi : un lien vers ses journaux,
    # où l'adresse figure encore.
    assert "message_id" not in evenement.payload


def test_the_pointer_to_the_crm_record_is_dropped(session, prospect_bavard):
    """Le CRM garde sa fiche — ce n'est pas à nous de la supprimer. Mais
    tant que le pointeur reste, la ligne anonymisée y mène d'un clic."""
    _purger(session)
    restant = session.execute(
        select(CRMMapping).where(CRMMapping.local_id == prospect_bavard.id)
    ).scalars()
    assert list(restant) == []


def test_a_blocked_draft_loses_the_reason_it_was_blocked(session, prospect_bavard):
    _purger(session)
    brouillon = session.execute(
        select(MessageDraft).where(MessageDraft.prospect_id == prospect_bavard.id)
    ).scalar_one()
    assert brouillon.blocked_reason == "[anonymisé]"
    assert brouillon.recipient_email is None
    assert brouillon.variables_used == {}


# --- Ce que la purge ne doit pas emporter -----------------------------------


def test_the_indicators_are_unchanged_by_the_deeper_anonymization(
    session, product_educore, prospect_bavard
):
    """Un effacement plus large ne doit pas réécrire les chiffres du passé.

    C'est la contrepartie du principe 3 de la politique : l'organisation et
    ses événements survivent précisément pour que le trimestre passé reste
    lisible.
    """
    from growth_agent.services.cohorts import compute_cohorts

    avant = compute_cohorts(session, product_id=product_educore.id, now=NOW)
    _purger(session)
    apres = compute_cohorts(session, product_id=product_educore.id, now=NOW)

    assert apres.blocked_by_reason == avant.blocked_by_reason
    assert [ligne.counts for ligne in apres.cohorts] == [ligne.counts for ligne in avant.cohorts]


def test_an_opposition_still_blocks_a_send_after_the_purge(
    session, product_educore, prospect_bavard
):
    """Vérification croisée avec la règle qui prime sur toutes les autres."""
    from growth_agent.models.enums import SuppressionReason, SuppressionScope
    from growth_agent.services.suppression import add_suppression, check_suppression

    add_suppression(
        session,
        value=MAIL,
        reason=SuppressionReason.OPT_OUT,
        scope=SuppressionScope.GLOBAL,
        source="test",
    )
    _purger(session)
    assert check_suppression(session, email=MAIL).suppressed is True
