"""Ce que le fournisseur d'expédition constate, et que nous ne savions pas.

Avant ce webhook, le système ne constatait que deux choses : qu'il avait
remis un message à SendGrid, et qu'il en avait bloqué un. Tout le reste du
parcours — réponse, rendez-vous, démonstration — reposait sur une saisie. La
page de suivi le disait, en toutes lettres, et c'était la bonne nouvelle :
elle le disait au lieu de le masquer.

Ces tests portent sur ce qui change, et surtout sur les quatre refus que la
route oppose : sans clé, sans signature valide, hors fenêtre temporelle, et
pour un message qui n'est pas de nous.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.email.sendgrid_events import (
    SendGridWebhookError,
    parse_events,
    verify_event_signature,
)
from growth_agent.models.enums import (
    DeliveryState,
    EventSource,
    OutreachEventType,
    SuppressionReason,
)
from growth_agent.models.outreach import OutreachEvent, SuppressionEntry
from growth_agent.services.suppression import check_suppression
from sendgrid_events_double import SendGridSigner, event

ROUTE = "/webhooks/sendgrid/events"
MESSAGE_ID = "W2k7LpQ8RaKq9fVtZ3bN1w"


@pytest.fixture
def signer() -> SendGridSigner:
    return SendGridSigner()


@pytest.fixture
def webhook_settings(signer) -> Settings:
    return Settings(sendgrid_event_public_key=signer.public_key_b64)


@pytest.fixture
def envoi(session, product_educore, make_prospect):
    """Un envoi réellement parti, avec l'identifiant rendu par SendGrid."""
    prospect = make_prospect(product=product_educore, contact_email="direction@college.example.org")
    evenement = OutreachEvent(
        product_id=product_educore.id,
        prospect_id=prospect.id,
        event_type=OutreachEventType.SENT,
        channel="email",
        occurred_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        actor="test",
        payload={"provider": "sendgrid"},
        is_simulated=False,
        delivery_state=DeliveryState.SENT,
        provider_message_id=MESSAGE_ID,
        idempotency_key=f"sent:{uuid.uuid4()}",
        event_source=EventSource.OBSERVED,
    )
    session.add(evenement)
    session.flush()
    session.commit()
    return evenement


@pytest.fixture
def poster(client, signer, webhook_settings):
    """Envoie une livraison signée à la route, comme SendGrid le ferait."""
    client.app.dependency_overrides[get_settings] = lambda: webhook_settings

    def _poster(events, *, timestamp=None, corps=None, entetes=None):
        livraison = signer.sign(events, timestamp=timestamp)
        return client.post(
            ROUTE,
            content=corps if corps is not None else livraison.body,
            headers={**livraison.headers, **(entetes or {})},
        )

    yield _poster
    client.app.dependency_overrides.pop(get_settings, None)


def _evenements(session, type_):
    return list(
        session.execute(select(OutreachEvent).where(OutreachEvent.event_type == type_)).scalars()
    )


# --- Les refus -------------------------------------------------------------


def test_without_a_configured_key_the_route_accepts_nothing(client, seeded, signer):
    """Un point d'entrée public qui écrit en base sur simple présentation
    d'un JSON permettrait de fausser les indicateurs et d'inscrire des
    oppositions au hasard. Sans clé, il est fermé."""
    livraison = signer.sign([event(message_id=MESSAGE_ID)])
    reponse = client.post(ROUTE, content=livraison.body, headers=livraison.headers)
    assert reponse.status_code == 503
    assert reponse.json()["status"] == "disabled"


def test_a_delivery_signed_by_another_key_is_refused(client, seeded, envoi, poster):
    autre = SendGridSigner()
    livraison = autre.sign([event(message_id=MESSAGE_ID)])
    reponse = client.post(ROUTE, content=livraison.body, headers=livraison.headers)
    assert reponse.status_code == 403


def test_a_body_modified_after_signing_is_refused(session, client, seeded, envoi, poster):
    """La vérification porte sur le corps brut, et c'est tout l'enjeu.

    Vérifier une charge resérialisée laisserait passer une modification de
    contenu sous une signature authentique.
    """
    reponse = poster(
        [event(message_id=MESSAGE_ID)],
        corps=b'[{"event":"delivered","sg_event_id":"forge","sg_message_id":"x","timestamp":1}]',
    )
    assert reponse.status_code == 403
    assert _evenements(session, OutreachEventType.DELIVERED) == []


def test_a_replayed_delivery_is_refused(session, client, seeded, envoi, poster):
    """Une requête authentique interceptée reste authentique : sans borne
    d'âge, elle se rejoue indéfiniment."""
    vieux = time.time() - 3600
    reponse = poster([event(message_id=MESSAGE_ID)], timestamp=vieux)
    assert reponse.status_code == 403
    assert _evenements(session, OutreachEventType.DELIVERED) == []


def test_a_refusal_does_not_say_which_check_failed(client, seeded, envoi, poster):
    """Distinguer « signature invalide » de « horodatage périmé »
    renseignerait qui cherche à forger une requête."""
    reponse = poster([event(message_id=MESSAGE_ID)], timestamp=time.time() - 3600)
    assert reponse.json() == {"status": "rejected"}


def test_a_signed_but_unreadable_body_is_refused_after_the_signature(
    session, client, seeded, envoi, signer, webhook_settings
):
    """L'ordre des contrôles compte : signer d'abord, lire ensuite.

    Lire le JSON avant de vérifier la signature ferait travailler l'analyseur
    sur une charge que personne n'a authentifiée.
    """
    client.app.dependency_overrides[get_settings] = lambda: webhook_settings
    livraison = signer.sign_body(b"ceci n'est pas du json")
    reponse = client.post(ROUTE, content=livraison.body, headers=livraison.headers)
    client.app.dependency_overrides.pop(get_settings, None)

    assert reponse.status_code == 400
    assert reponse.json()["status"] == "unreadable"
    assert _evenements(session, OutreachEventType.DELIVERED) == []


# --- Le rattachement -------------------------------------------------------


def test_a_delivery_is_recorded_as_observed(session, client, seeded, envoi, poster):
    """C'est tout l'intérêt : ce fait-là, personne ne l'a saisi."""
    reponse = poster([event(message_id=MESSAGE_ID)])
    assert reponse.status_code == 200
    assert reponse.json()["accepted"] == 1

    remis = _evenements(session, OutreachEventType.DELIVERED)
    assert len(remis) == 1
    assert remis[0].event_source == EventSource.OBSERVED
    assert remis[0].prospect_id == envoi.prospect_id
    assert remis[0].product_id == envoi.product_id


def test_the_node_suffix_sendgrid_adds_does_not_break_the_match(
    session, client, seeded, envoi, poster
):
    """Le détail qui échoue en silence.

    L'API d'envoi répond l'identifiant nu ; les événements le renvoient
    suffixé par le nœud qui a traité le message. Comparés tels quels, ils ne
    se rapprochent jamais — et le symptôme est muet : des événements qui
    arrivent, et aucun qui se rattache.
    """
    suffixe = poster([event(message_id=MESSAGE_ID, event_id="a", suffixed=True)])
    assert suffixe.json()["accepted"] == 1
    nu = poster([event(message_id=MESSAGE_ID, event_id="b", suffixed=False)])
    assert nu.json()["accepted"] == 1
    assert len(_evenements(session, OutreachEventType.DELIVERED)) == 2


def test_an_event_for_a_message_that_is_not_ours_is_discarded(
    session, client, seeded, envoi, poster
):
    """Le même compte SendGrid sert aussi au transactionnel de Maintrix.

    Absorber ces événements rattacherait à des prospects des faits qui
    concernent des comptes utilisateurs.
    """
    reponse = poster([event(message_id="message-d-un-autre-systeme")])
    assert reponse.json() == {
        **reponse.json(),
        "accepted": 0,
        "unmatched": 1,
    }
    assert _evenements(session, OutreachEventType.DELIVERED) == []


def test_an_event_for_an_abandoned_reservation_is_discarded(
    session, client, seeded, product_educore, make_prospect, poster
):
    """Une réservation restée « incertaine » n'a jamais été remise au
    fournisseur : elle ne peut pas avoir produit d'événement."""
    prospect = make_prospect(product=product_educore)
    session.add(
        OutreachEvent(
            product_id=product_educore.id,
            prospect_id=prospect.id,
            event_type=OutreachEventType.SENT,
            channel="email",
            occurred_at=datetime.now(UTC),
            actor="test",
            payload={},
            is_simulated=False,
            delivery_state=DeliveryState.UNCERTAIN,
            provider_message_id="reservation-sans-suite",
        )
    )
    session.flush()
    session.commit()

    reponse = poster([event(message_id="reservation-sans-suite")])
    assert reponse.json()["unmatched"] == 1
    assert _evenements(session, OutreachEventType.DELIVERED) == []


def test_sendgrid_replaying_a_delivery_does_not_double_the_figures(
    session, client, seeded, envoi, poster
):
    """SendGrid rejoue toute livraison qu'il croit perdue."""
    lot = [event(message_id=MESSAGE_ID, event_id="evt-unique")]
    assert poster(lot).json()["accepted"] == 1
    second = poster(lot).json()
    assert second["accepted"] == 0
    assert second["duplicates"] == 1
    assert len(_evenements(session, OutreachEventType.DELIVERED)) == 1


# --- Ce qui est écarté à la frontière --------------------------------------


@pytest.mark.parametrize("kind", ["processed", "deferred", "click"])
def test_events_outside_the_contract_are_discarded(session, client, seeded, envoi, poster, kind):
    """« click » en particulier : suivre les clics est un traitement du
    comportement du destinataire, que ce projet ne pratique pas."""
    reponse = poster([event(message_id=MESSAGE_ID, kind=kind)])
    assert reponse.json()["ignored"] == 1
    assert reponse.json()["accepted"] == 0


def test_opens_are_counted_but_not_absorbed_while_tracking_is_off(
    session, client, seeded, envoi, poster
):
    """Une ouverture ne se mesure qu'en insérant un pixel de suivi.

    Elles sont comptées pour que l'écart se voie au lieu de se deviner.
    """
    reponse = poster([event(message_id=MESSAGE_ID, kind="open")])
    assert reponse.json()["opens_discarded"] == 1
    assert _evenements(session, OutreachEventType.OPENED) == []


def test_opens_are_absorbed_once_tracking_is_decided(
    session, client, seeded, envoi, signer, client_with_tracking
):
    reponse = client_with_tracking([event(message_id=MESSAGE_ID, kind="open")])
    assert reponse.json()["accepted"] == 1
    assert len(_evenements(session, OutreachEventType.OPENED)) == 1


@pytest.fixture
def client_with_tracking(client, signer):
    reglages = Settings(sendgrid_event_public_key=signer.public_key_b64, sendgrid_track_opens=True)
    client.app.dependency_overrides[get_settings] = lambda: reglages

    def _poster(events):
        livraison = signer.sign(events)
        return client.post(ROUTE, content=livraison.body, headers=livraison.headers)

    yield _poster
    client.app.dependency_overrides.pop(get_settings, None)


def test_a_malformed_entry_does_not_lose_the_rest_of_the_batch(
    session, client, seeded, envoi, poster
):
    """Refuser tout le lot ferait perdre les autres lignes, et SendGrid
    rejouerait le lot entier sans fin."""
    reponse = poster(
        [
            {"event": "delivered"},  # sans identifiant ni message
            "pas un objet",
            event(message_id=MESSAGE_ID, event_id="valide"),
        ]
    )
    assert reponse.json()["accepted"] == 1
    assert reponse.json()["total"] == 1, "les lignes illisibles n'entrent même pas"


# --- Les oppositions -------------------------------------------------------


def test_a_hard_bounce_stops_further_sending(session, client, seeded, envoi, poster):
    """Continuer d'écrire à une adresse qui n'existe pas abîme la réputation
    d'expédition de tout le domaine, celle de Maintrix comprise."""
    reponse = poster([event(message_id=MESSAGE_ID, kind="bounce", bounce_type="bounce")])
    assert reponse.json()["oppositions"] == 1
    assert len(_evenements(session, OutreachEventType.BOUNCED)) == 1

    verdict = check_suppression(session, email="direction@college.example.org")
    assert verdict.suppressed is True
    assert verdict.reason == SuppressionReason.BOUNCE_HARD


def test_a_temporary_block_does_not_create_an_opposition(session, client, seeded, envoi, poster):
    """Une boîte pleine se vide : retirer l'organisation de la prospection
    pour un refus temporaire serait une perte sèche."""
    reponse = poster([event(message_id=MESSAGE_ID, kind="bounce", bounce_type="blocked")])
    assert reponse.json()["oppositions"] == 0
    assert len(_evenements(session, OutreachEventType.BOUNCED)) == 1
    assert check_suppression(session, email="direction@college.example.org").suppressed is False


def test_a_drop_is_recorded_without_assuming_why(session, client, seeded, envoi, poster):
    """Nous n'avons pas la raison. Inscrire une opposition sur une
    supposition retirerait une organisation sans qu'elle l'ait demandé."""
    reponse = poster([event(message_id=MESSAGE_ID, kind="dropped")])
    assert reponse.json()["accepted"] == 1
    assert reponse.json()["oppositions"] == 0
    assert check_suppression(session, email="direction@college.example.org").suppressed is False


def test_a_spam_report_is_an_opposition(session, client, seeded, envoi, poster):
    reponse = poster([event(message_id=MESSAGE_ID, kind="spamreport")])
    assert reponse.json()["oppositions"] == 1
    assert len(_evenements(session, OutreachEventType.OPTED_OUT)) == 1
    verdict = check_suppression(session, email="direction@college.example.org")
    assert verdict.reason == SuppressionReason.COMPLAINT


def test_an_unsubscribe_through_an_unexpected_path_is_still_honoured(
    session, client, seeded, envoi, poster
):
    """Growth Agent n'utilise pas la désinscription de SendGrid : chaque
    message porte son propre lien. Si l'événement arrive tout de même,
    quelqu'un a demandé à ne plus être contacté, et l'ignorer parce qu'il
    emprunte un chemin inattendu serait indéfendable."""
    reponse = poster([event(message_id=MESSAGE_ID, kind="unsubscribe")])
    assert reponse.json()["oppositions"] == 1
    assert check_suppression(session, email="direction@college.example.org").suppressed is True


def test_the_opposed_address_is_read_from_our_records_not_from_the_payload(
    session, client, seeded, envoi, poster
):
    """La signature authentifie l'expéditeur, pas le contenu d'un compte
    SendGrid compromis. L'adresse vient donc de notre prospect."""
    reponse = poster(
        [
            event(
                message_id=MESSAGE_ID,
                kind="spamreport",
                email="victime-choisie@autre-etablissement.example.org",
            )
        ]
    )
    assert reponse.json()["oppositions"] == 1
    inscrites = {
        entree.normalized_value for entree in session.execute(select(SuppressionEntry)).scalars()
    }
    assert "direction@college.example.org" in inscrites
    assert "victime-choisie@autre-etablissement.example.org" not in inscrites


# --- Ce qui n'est pas stocké ----------------------------------------------


def test_the_providers_free_text_reason_never_reaches_the_database(
    session, client, seeded, envoi, poster
):
    """SendGrid renvoie « 550 5.1.1 <direction@college.example.org> user
    unknown » : l'adresse y figure. Seul un code est conservé, conformément
    à la liste blanche de la politique de rétention."""
    from growth_agent.retention.policy import KEPT_EVENT_PAYLOAD_KEYS

    poster(
        [
            event(
                message_id=MESSAGE_ID,
                kind="bounce",
                bounce_type="bounce",
                reason="550 5.1.1 <direction@college.example.org> user unknown",
            )
        ]
    )
    rejet = _evenements(session, OutreachEventType.BOUNCED)[0]
    assert rejet.payload == {"provider": "sendgrid", "reason_code": "bounce"}
    assert set(rejet.payload) <= KEPT_EVENT_PAYLOAD_KEYS
    assert "college.example.org" not in str(rejet.payload)


# --- Effet sur les indicateurs --------------------------------------------


def test_a_bounce_does_not_rewrite_the_contact_count(
    session, client, seeded, envoi, product_educore, poster
):
    """Un rejet qui arrive deux heures après l'envoi ferait baisser tout
    seul un chiffre déjà lu. Il est compté à côté."""
    from growth_agent.services.cohorts import compute_cohorts

    maintenant = datetime(2026, 10, 20, tzinfo=UTC)
    avant = compute_cohorts(session, product_id=product_educore.id, now=maintenant)
    poster([event(message_id=MESSAGE_ID, kind="bounce", bounce_type="bounce")])
    apres = compute_cohorts(session, product_id=product_educore.id, now=maintenant)

    assert [ligne.counts for ligne in apres.cohorts] == [ligne.counts for ligne in avant.cohorts]
    assert apres.side_facts["bounced"] == 1
    assert any("rejeté(s) par le serveur" in note for note in apres.notes)


def test_a_delivery_makes_the_report_say_what_it_confirmed(
    session, client, seeded, envoi, product_educore, poster
):
    from growth_agent.services.cohorts import compute_cohorts

    poster([event(message_id=MESSAGE_ID)])
    rapport = compute_cohorts(
        session, product_id=product_educore.id, now=datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert rapport.side_facts["delivered"] == 1
    assert rapport.by_source[EventSource.OBSERVED] >= 2


# --- La vérification, isolée ----------------------------------------------


def test_a_valid_signature_passes(signer):
    livraison = signer.sign([event(message_id=MESSAGE_ID)])
    verify_event_signature(
        public_key_b64=signer.public_key_b64,
        signature_b64=livraison.headers["X-Twilio-Email-Event-Webhook-Signature"],
        timestamp=livraison.headers["X-Twilio-Email-Event-Webhook-Timestamp"],
        body=livraison.body,
    )


@pytest.mark.parametrize(
    ("surcharges", "attendu"),
    [
        ({"public_key_b64": ""}, "Aucune clé publique"),
        ({"signature_b64": ""}, "manquant"),
        ({"timestamp": ""}, "manquant"),
        ({"timestamp": "pas-un-nombre"}, "illisible"),
        ({"public_key_b64": "pas-du-base64!!"}, "illisible"),
        ({"signature_b64": "YWJj"}, "invalide"),
    ],
)
def test_every_way_a_delivery_can_be_wrong_is_refused(signer, surcharges, attendu):
    livraison = signer.sign([event(message_id=MESSAGE_ID)])
    arguments = {
        "public_key_b64": signer.public_key_b64,
        "signature_b64": livraison.headers["X-Twilio-Email-Event-Webhook-Signature"],
        "timestamp": livraison.headers["X-Twilio-Email-Event-Webhook-Timestamp"],
        "body": livraison.body,
        **surcharges,
    }
    with pytest.raises(SendGridWebhookError, match=attendu):
        verify_event_signature(**arguments)


def test_the_clock_window_cuts_both_ways(signer):
    """Une horloge en avance est aussi suspecte qu'une horloge en retard."""
    for decalage in (-3600, 3600):
        livraison = signer.sign([event(message_id=MESSAGE_ID)], timestamp=time.time() + decalage)
        with pytest.raises(SendGridWebhookError, match="fenêtre"):
            verify_event_signature(
                public_key_b64=signer.public_key_b64,
                signature_b64=livraison.headers["X-Twilio-Email-Event-Webhook-Signature"],
                timestamp=livraison.headers["X-Twilio-Email-Event-Webhook-Timestamp"],
                body=livraison.body,
            )


def test_reading_a_delivery_discards_what_it_cannot_read():
    assert parse_events("pas une liste") == []
    assert parse_events([{"event": "delivered"}]) == []
    assert parse_events([{"sg_event_id": "a", "event": "delivered"}]) == []
    lu = parse_events([event(message_id=MESSAGE_ID, at=1_760_000_000, bounce_type="bounce")])
    assert len(lu) == 1
    assert lu[0].message_id == MESSAGE_ID
    assert lu[0].bounce_type == "bounce"
    assert lu[0].occurred_at == datetime.fromtimestamp(1_760_000_000, tz=UTC)


def test_what_is_read_carries_no_personal_data():
    """Une structure qui ne porte pas une donnée ne peut pas la laisser fuir."""
    lu = parse_events(
        [
            event(
                message_id=MESSAGE_ID,
                email="quelquun@exemple.example.org",
                reason="550 user unknown",
            )
        ]
    )
    assert "quelquun" not in str(lu[0])
    assert not hasattr(lu[0], "email")


def test_the_tolerance_is_wider_than_the_product_webhooks():
    """SendGrid réessaie une livraison en échec : une fenêtre trop étroite
    transformerait chaque incident réseau en événements perdus."""
    from growth_agent.connectors.email.sendgrid_events import DEFAULT_TOLERANCE_SECONDS
    from growth_agent.connectors.product.webhooks import (
        DEFAULT_TOLERANCE_SECONDS as PRODUIT,
    )

    assert DEFAULT_TOLERANCE_SECONDS > PRODUIT
    assert timedelta(seconds=DEFAULT_TOLERANCE_SECONDS) <= timedelta(minutes=15)
