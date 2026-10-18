"""Ce que le système a constaté, et ce qu'une personne a affirmé.

Un tableau qui affiche « 4 démonstrations » à côté de « 4 envois » laisse
croire que les deux chiffres se valent. Ils ne se valent pas : l'envoi, le
système l'a fait lui-même et il en garde la trace ; la démonstration, une
personne l'a saisie après coup, et rien ne prouve qu'elle a eu lieu — ni
qu'une autre, non saisie, n'a pas eu lieu.

La direction commerciale lit ces chiffres pour décider. Elle doit savoir
lesquels reposent sur un fait technique et lesquels sur une déclaration.
Ces tests portent sur cette distinction, et sur ce qu'elle change à la
lecture du rapport.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from growth_agent.models.enums import (
    ApprovalSubject,
    DeliveryState,
    EventSource,
    OpportunityStage,
    OutreachEventType,
)
from growth_agent.models.outreach import MessageDraft, OutreachEvent
from growth_agent.services.approvals import decide_approval, request_approval
from growth_agent.services.cohorts import JOURNEY, SIDE_FACTS, compute_cohorts
from growth_agent.services.outreach import send_draft
from growth_agent.services.pipeline import DemoReport, open_opportunity, record_demo

ACTEUR = "merveille@techlearn-saem.com"
PORTEUR = "awa@techlearn-saem.com"


def _event(session, product, prospect, event_type, when, *, source, campaign=None):
    session.add(
        OutreachEvent(
            product_id=product.id,
            campaign_id=campaign.id if campaign else None,
            prospect_id=prospect.id,
            event_type=event_type,
            channel="email",
            occurred_at=when,
            actor="test",
            payload={},
            is_simulated=False,
            event_source=source,
            delivery_state=(DeliveryState.SENT if event_type == OutreachEventType.SENT else None),
        )
    )
    session.flush()


def _report(session, product, **kwargs):
    return compute_cohorts(session, product_id=product.id, product_code=product.code, **kwargs)


def _sent_draft(session, product, prospect, email_provider, settings):
    """Fait passer un brouillon par le vrai chemin d'envoi (simulé)."""
    draft = MessageDraft(
        product_id=product.id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        recipient_email=prospect.contact_email,
        subject="Objet",
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
        session, approval_id=approval.id, decided_by="valideur@exemple.example.org", approve=True
    )
    send_draft(
        session,
        draft=draft,
        email_provider=email_provider,
        actor=ACTEUR,
        settings=settings,
    )
    return draft


# --- À l'écriture ----------------------------------------------------------


def test_what_the_system_does_itself_is_recorded_as_observed(
    session, product_educore, make_prospect, email, settings
):
    """Le service d'envoi est le seul témoin de l'envoi : il le constate."""
    prospect = make_prospect(product=product_educore)
    draft = _sent_draft(session, product_educore, prospect, email, settings)

    evenement = (
        session.query(OutreachEvent)
        .filter(
            OutreachEvent.draft_id == draft.id,
            OutreachEvent.event_type == OutreachEventType.SENT,
        )
        .one()
    )
    assert evenement.event_source == EventSource.OBSERVED


def test_a_stage_someone_types_in_is_recorded_as_declared(session, product_educore, make_prospect):
    """Le système ne voit pas une démonstration se tenir : on la lui dit."""
    prospect = make_prospect(product=product_educore)
    dossier = open_opportunity(session, prospect=prospect, owner_email=PORTEUR, actor=ACTEUR)
    record_demo(session, opportunity=dossier, report=DemoReport(held=True), actor=ACTEUR)

    evenement = (
        session.query(OutreachEvent)
        .filter(OutreachEvent.event_type == OutreachEventType.DEMO_HELD)
        .one()
    )
    assert evenement.event_source == EventSource.DECLARED


def test_the_migration_backfill_agrees_with_what_the_code_writes(
    session, product_educore, make_prospect, email, settings
):
    """Garde-fou de dérive.

    La migration `0006` déduit l'origine du type d'événement, pour les lignes
    écrites avant la colonne. Si le code se met un jour à écrire `sent` en
    `declared`, ou l'inverse, les lignes anciennes et les nouvelles cesseront
    de vouloir dire la même chose — et personne ne s'en apercevrait, puisque
    les deux seraient individuellement cohérentes.
    """
    source = Path(__file__).resolve().parents[1] / "migrations/versions/0006_event_source.py"
    arbre = ast.parse(source.read_text(encoding="utf-8"))
    declares_observes: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Assign) and any(
            isinstance(cible, ast.Name) and cible.id == "OBSERVED_TYPES" for cible in noeud.targets
        ):
            declares_observes = set(ast.literal_eval(noeud.value))
    assert declares_observes, "OBSERVED_TYPES introuvable dans la migration 0006"

    # On fait écrire au code réel ce qu'il écrit, puis on confronte.
    prospect = make_prospect(product=product_educore)
    _sent_draft(session, product_educore, prospect, email, settings)
    dossier = open_opportunity(session, prospect=prospect, owner_email=PORTEUR, actor=ACTEUR)
    record_demo(session, opportunity=dossier, report=DemoReport(held=True), actor=ACTEUR)

    for evenement in session.query(OutreachEvent).all():
        attendu = (
            EventSource.OBSERVED
            if evenement.event_type in declares_observes
            else EventSource.DECLARED
        )
        assert evenement.event_source == attendu, (
            f"{evenement.event_type} écrit en {evenement.event_source}, "
            f"mais la migration le rangerait en {attendu}"
        )


# --- À la lecture ----------------------------------------------------------


def test_the_report_says_how_many_of_each_stage_it_actually_saw(
    session, product_educore, make_prospect
):
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    aout = datetime(2026, 8, 4, tzinfo=UTC)
    for index in range(2):
        prospect = make_prospect(product=product_educore, domain=f"o{index}.example.org")
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.SENT,
            aout,
            source=EventSource.OBSERVED,
        )
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.DEMO_HELD,
            aout + timedelta(days=9),
            source=EventSource.DECLARED,
        )

    ligne = _report(session, product_educore, now=maintenant).cohorts[0]
    assert ligne.counts["contacted"] == 2
    assert ligne.observed["contacted"] == 2
    # Deux démonstrations comptées, aucune constatée : le chiffre repose
    # entièrement sur la saisie de deux personnes.
    assert ligne.counts["demo_held"] == 2
    assert ligne.observed["demo_held"] == 0


def test_what_was_observed_never_exceeds_what_was_counted(session, product_educore, make_prospect):
    """Cohérence d'indicateur : la part constatée est une part du total.

    Une erreur d'agrégation afficherait « 3 constatés sur 2 » sans rien
    casser par ailleurs — un chiffre impossible, lu comme rassurant.
    """
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    aout = datetime(2026, 8, 4, tzinfo=UTC)
    for index in range(3):
        prospect = make_prospect(product=product_educore, domain=f"c{index}.example.org")
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.SENT,
            aout,
            source=EventSource.OBSERVED,
        )
        _event(
            session,
            product_educore,
            prospect,
            OutreachEventType.REPLIED,
            aout + timedelta(days=2),
            source=EventSource.DECLARED if index else EventSource.OBSERVED,
        )

    rapport = _report(session, product_educore, now=maintenant)
    for ligne in rapport.cohorts:
        for etape, _ in JOURNEY:
            assert 0 <= ligne.observed[etape] <= ligne.counts[etape], (
                f"{ligne.cohort}/{etape} : {ligne.observed[etape]} constatés "
                f"pour {ligne.counts[etape]} comptés"
            )


def test_a_report_made_mostly_of_declarations_says_so(session, product_educore, make_prospect):
    """Sans cette note, « 1 réponse sur 1 envoi » se lirait comme un fait."""
    aout = datetime(2026, 8, 4, tzinfo=UTC)
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        aout,
        source=EventSource.OBSERVED,
    )
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.REPLIED,
        aout + timedelta(days=1),
        source=EventSource.DECLARED,
    )

    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert rapport.by_source == {EventSource.OBSERVED: 1, EventSource.DECLARED: 1}
    assert any("50 %" in note and "saisis par une personne" in note for note in rapport.notes)


def test_a_fully_observed_report_carries_no_declaration_warning(
    session, product_educore, make_prospect
):
    """L'avertissement doit vouloir dire quelque chose : affiché partout,
    il ne serait plus lu."""
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        datetime(2026, 8, 4, tzinfo=UTC),
        source=EventSource.OBSERVED,
    )
    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert not any("saisis par une personne" in note for note in rapport.notes)


# --- Les rendez-vous manqués ----------------------------------------------


def test_a_missed_meeting_is_counted_apart_from_demos(session, product_educore, make_prospect):
    """Ni une démonstration tenue, ni une absence de démonstration.

    Compté dans `demo_held`, il gonflerait le taux de démonstration ; ignoré,
    il disparaîtrait et on ne saurait jamais combien de créneaux se perdent.
    """
    prospect = make_prospect(product=product_educore)
    _event(
        session,
        product_educore,
        prospect,
        OutreachEventType.SENT,
        datetime(2026, 8, 4, tzinfo=UTC),
        source=EventSource.OBSERVED,
    )
    dossier = open_opportunity(session, prospect=prospect, owner_email=PORTEUR, actor=ACTEUR)
    record_demo(
        session,
        opportunity=dossier,
        report=DemoReport(held=False),
        actor=ACTEUR,
        now=datetime(2026, 8, 20, tzinfo=UTC),
    )

    rapport = _report(session, product_educore, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert rapport.side_facts["demo_no_show"] == 1
    assert rapport.cohorts[0].counts["demo_held"] == 0
    assert dossier.stage == OpportunityStage.OPEN
    assert any("rendez-vous manqué" in note for note in rapport.notes)


def test_every_side_fact_is_kept_out_of_the_funnel(session, product_educore, make_prospect):
    """Cohérence d'indicateur : aucun fait annexe ne doit être une étape.

    Ajouter demain un fait annexe à `JOURNEY` par inadvertance le ferait
    entrer dans les taux de conversion sans qu'aucun test ne proteste.
    """
    etapes = {event for _, event in JOURNEY}
    faits = {event for _, event in SIDE_FACTS}
    assert not etapes & faits, f"compté deux fois : {sorted(etapes & faits)}"


# --- Le filtre par campagne -----------------------------------------------


def test_the_campaign_filter_isolates_one_campaign(
    session, product_educore, campaign_educore, make_prospect
):
    maintenant = datetime(2026, 10, 18, tzinfo=UTC)
    aout = datetime(2026, 8, 4, tzinfo=UTC)
    dedans = make_prospect(product=product_educore, domain="dedans.example.org")
    dehors = make_prospect(product=product_educore, domain="dehors.example.org")
    _event(
        session,
        product_educore,
        dedans,
        OutreachEventType.SENT,
        aout,
        source=EventSource.OBSERVED,
        campaign=campaign_educore,
    )
    _event(
        session,
        product_educore,
        dehors,
        OutreachEventType.SENT,
        aout,
        source=EventSource.OBSERVED,
    )

    entier = _report(session, product_educore, now=maintenant)
    filtre = _report(
        session,
        product_educore,
        now=maintenant,
        campaign_id=campaign_educore.id,
        campaign_slug=campaign_educore.slug,
    )
    assert entier.cohorts[0].counts["contacted"] == 2
    assert filtre.cohorts[0].counts["contacted"] == 1
    assert filtre.campaign_slug == campaign_educore.slug


# --- Ce que l'API en expose -----------------------------------------------


def test_an_unknown_campaign_is_refused_rather_than_silently_ignored(client, seeded, auth):
    """Un filtre ignoré renverrait le rapport entier sous l'étiquette d'une
    campagne : le lecteur attribuerait à sa campagne des chiffres qui ne
    sont pas les siens."""
    reponse = client.get(
        "/api/v1/cohorts?product_code=educore&campaign_slug=campagne-qui-nexiste-pas",
        headers=auth("lecteur"),
    )
    assert reponse.status_code == 404
    assert "ampagne" in reponse.json()["detail"]


def test_the_cohort_payload_carries_everything_the_screen_needs(client, seeded, auth):
    """Contrat de lecture : la page « Performance commerciale » lit ces clés.

    En supprimer une du rapport laisserait l'écran afficher du vide sans
    erreur — la pire panne, puisqu'elle se lit comme une absence de
    résultats.
    """
    reponse = client.get("/api/v1/cohorts?product_code=educore", headers=auth("lecteur"))
    assert reponse.status_code == 200
    corps = reponse.json()
    for cle in (
        "product_code",
        "campaign_slug",
        "computed_at",
        "cohorts",
        "by_source",
        "side_facts",
        "median_days",
        "loss_reasons",
        "blocked_by_reason",
        "open_pipeline",
        "overdue_by_owner",
        "notes",
    ):
        assert cle in corps, f"clé absente du rapport : {cle}"


def test_reading_the_cohorts_requires_being_logged_in(client, seeded):
    assert client.get("/api/v1/cohorts?product_code=educore").status_code == 401


# --- Ce que l'écran lit, et ce que l'API écrit ----------------------------


def _page_performance() -> str:
    chemin = Path(__file__).resolve().parents[2] / "web/src/pages/Performance.tsx"
    return chemin.read_text(encoding="utf-8")


def test_the_screen_reads_the_stages_the_api_actually_produces():
    """Garde-fou de dérive entre les deux langages.

    Renommer une étape côté Python laisserait la page lire une clé absente :
    `row.counts['demo_held']` vaut alors `undefined`, l'interface affiche 0,
    et la direction conclut qu'aucune démonstration n'a eu lieu. Aucune
    erreur nulle part — le pire des cas.
    """
    import re

    trouve = re.search(
        r"const STAGES: Array<\{ key: string; label: string \}> = \[(.*?)\]",
        _page_performance(),
        re.DOTALL,
    )
    assert trouve, "STAGES introuvable dans apps/web/src/pages/Performance.tsx"
    cote_web = re.findall(r"key: '([a-z_]+)'", trouve.group(1))
    assert cote_web == [nom for nom, _ in JOURNEY], (
        f"parcours divergent — API {[nom for nom, _ in JOURNEY]}, interface {cote_web}"
    )


def test_the_screen_reads_the_median_keys_the_api_actually_produces(client, seeded, auth):
    """Même garde-fou pour les délais : une clé inventée afficherait
    « donnée insuffisante » pour une étape pourtant mesurée."""
    import re

    trouve = re.search(
        r"const DELAY_LABELS: Record<string, string> = \{(.*?)\n\}",
        _page_performance(),
        re.DOTALL,
    )
    assert trouve, "DELAY_LABELS introuvable dans la page"
    cote_web = set(re.findall(r"^\s{2}([a-z_]+):", trouve.group(1), re.MULTILINE))

    corps = client.get("/api/v1/cohorts?product_code=educore", headers=auth("lecteur")).json()
    assert cote_web == set(corps["median_days"]), (
        f"délais divergents — API {sorted(corps['median_days'])}, interface {sorted(cote_web)}"
    )
