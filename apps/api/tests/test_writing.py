"""Détection des tournures qui trahissent un texte généré.

Le danger de ce genre de contrôle est le faux positif : un détecteur trop
zélé rejetterait des messages parfaitement écrits et rendrait l'outil
inutilisable. La moitié de ces tests porte donc sur ce qui doit passer.
"""

from __future__ import annotations

import pytest

from growth_agent.services.writing import (
    MAX_WORDS_BLOCKING,
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    analyze_message,
)

BON_MESSAGE = """Bonjour,

En lisant votre offre d'emploi, j'ai noté qu'une GMAO sera déployée en 2027.

Maintrix sert à suivre les interventions et les arrêts machine sans passer par
un tableur. Si c'est utile, je vous montre le suivi d'un arrêt sur un de vos
équipements, en 30 minutes.

Est-ce un sujet d'actualité chez vous ?

Si ce n'est pas votre sujet, répondez STOP et je ne vous recontacte pas.

Paul Nkeng
TechLearn"""


def codes(report) -> set[str]:
    return {f.code for f in report.findings}


# --- Ce qui doit passer ------------------------------------------------------


def test_a_well_written_message_scores_full_marks():
    report = analyze_message(
        BON_MESSAGE,
        subject="Votre offre — technicien de maintenance",
        organization_name="Port Autonome de Kribi Services",
        product_name="Maintrix",
    )
    assert report.naturalness == 100
    assert report.findings == []
    assert report.is_blocking is False


def test_a_short_direct_message_is_not_penalized():
    message = """Bonjour,

Qui suit la maintenance chez vous ? Je cherche la bonne personne.

Répondez STOP si le sujet ne vous concerne pas.

Awa Njoya
TechLearn"""
    assert analyze_message(message).is_blocking is False


def test_a_single_connector_is_tolerated():
    """« Ainsi » tout seul n'a rien d'une signature de machine."""
    message = """Bonjour,

Votre site mentionne une refonte du suivi des absences. Ainsi nous pourrions
vous être utiles sur ce point précis.

Répondez STOP pour ne plus rien recevoir.

Awa Njoya
TechLearn"""
    assert "connecteurs_empiles" not in codes(analyze_message(message))


# --- Clichés rédhibitoires ---------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "Je me permets de vous contacter au sujet de votre établissement.",
        "J'espère que ce message vous trouve bien.",
        "Dans un monde où tout s'accélère, la maintenance évolue.",
        "Je reste à votre entière disposition pour en discuter.",
        "N'hésitez pas à me contacter pour échanger.",
        "Notre solution innovante transforme la gestion scolaire.",
        "Nous allons révolutionner votre gestion des absences.",
        "Un outil parfaitement adapté à vos besoins.",
    ],
)
def test_known_cliches_block_the_draft(phrase):
    message = f"Bonjour,\n\n{phrase}\n\nRépondez STOP.\n\nAwa Njoya\nTechLearn"
    report = analyze_message(message)
    assert report.is_blocking is True
    assert "cliche_redhibitoire" in codes(report)


def test_every_cliche_comes_with_a_replacement():
    """Signaler sans proposer d'alternative ne fait pas progresser le texte."""
    report = analyze_message(
        "Bonjour,\n\nJe me permets de vous contacter.\n\nSTOP\n\nAwa Njoya\nTechLearn"
    )
    for finding in report.findings:
        assert len(finding.suggestion) > 20, f"{finding.code} sans suggestion utile"


def test_detection_ignores_case_and_accents():
    report = analyze_message(
        "Bonjour,\n\nJE ME PERMETS DE VOUS ECRIRE.\n\nSTOP\n\nAwa Njoya\nTechLearn"
    )
    assert "cliche_redhibitoire" in codes(report)


# --- Signature ---------------------------------------------------------------


def test_a_closing_without_a_name_blocks():
    """Le marqueur le plus net d'un envoi automatique."""
    message = "Bonjour,\n\nUn mot rapide.\n\nRépondez STOP.\n\nBien cordialement,"
    report = analyze_message(message)
    assert report.is_blocking is True
    assert "signature_absente" in codes(report)


def test_a_closing_followed_by_a_name_passes():
    message = "Bonjour,\n\nUn mot rapide.\n\nRépondez STOP.\n\nCordialement,\nAwa Njoya"
    assert "signature_absente" not in codes(analyze_message(message))


def test_a_message_without_any_closing_formula_is_not_flagged():
    """Signer sans formule de politesse est courant et parfaitement correct."""
    message = "Bonjour,\n\nUn mot rapide.\n\nRépondez STOP.\n\nAwa Njoya\nTechLearn"
    assert "signature_absente" not in codes(analyze_message(message))


# --- Forme -------------------------------------------------------------------


def test_an_overlong_message_blocks():
    message = (
        "Bonjour,\n\n"
        + " ".join(["Nous travaillons sur ce sujet depuis longtemps."] * 40)
        + "\n\nRépondez STOP.\n\nAwa Njoya"
    )
    report = analyze_message(message)
    assert report.word_count > MAX_WORDS_BLOCKING
    assert "message_trop_long" in codes(report)
    assert report.is_blocking is True


def test_uniform_sentence_lengths_are_flagged():
    """Un humain alterne long et court ; un modèle produit un rythme régulier."""
    message = (
        "Bonjour. "
        "Nous aidons les établissements à gérer leurs inscriptions correctement. "
        "Nous aidons les directions à suivre les absences de façon efficace. "
        "Nous aidons les équipes à réduire le temps passé sur ces tâches. "
        "Nous aidons les écoles à moderniser leur gestion administrative ainsi. "
        "Répondez STOP pour ne plus rien recevoir.\n\nAwa Njoya"
    )
    assert "rythme_uniforme" in codes(analyze_message(message))


def test_varied_rhythm_is_not_flagged():
    assert "rythme_uniforme" not in codes(analyze_message(BON_MESSAGE))


def test_stacked_connectors_are_flagged():
    message = """Bonjour,

De plus, nous intervenons vite. En outre, le déploiement est rapide. Par
ailleurs, nos tarifs sont clairs.

Répondez STOP.

Awa Njoya"""
    assert "connecteurs_empiles" in codes(analyze_message(message))


def test_a_long_raw_url_is_flagged():
    message = (
        "Bonjour,\n\nVoir https://etablissement.example.org/projet-detablissement-2026-v2\n\n"
        "Répondez STOP.\n\nAwa Njoya"
    )
    report = analyze_message(message)
    assert "url_brute" in codes(report)
    assert report.is_blocking is False, "maladroit, pas disqualifiant"


def test_emoji_is_flagged():
    message = "Bonjour,\n\nUn point rapide 🚀\n\nRépondez STOP.\n\nAwa Njoya"
    assert "emoji" in codes(analyze_message(message))


def test_a_repeated_variable_is_flagged():
    """Répéter le nom de l'organisation trahit un texte assemblé."""
    message = """Bonjour,

Le Collège Les Palmiers nous intéresse. Le Collège Les Palmiers grandit vite.
Nous pensons que le Collège Les Palmiers gagnerait du temps.

Répondez STOP.

Awa Njoya"""
    report = analyze_message(message, organization_name="Collège Les Palmiers")
    assert "repetition_variable" in codes(report)


def test_a_subject_starting_with_the_product_name_is_flagged():
    report = analyze_message(
        BON_MESSAGE, subject="Maintrix — démonstration", product_name="Maintrix"
    )
    assert "objet_centre_produit" in codes(report)


def test_a_subject_about_the_prospect_is_not_flagged():
    report = analyze_message(
        BON_MESSAGE, subject="Votre offre de technicien", product_name="Maintrix"
    )
    assert "objet_centre_produit" not in codes(report)


# --- Score -------------------------------------------------------------------


def test_the_score_falls_with_each_finding():
    propre = analyze_message(BON_MESSAGE).naturalness
    sale = analyze_message(
        "Bonjour,\n\nJe me permets de vous contacter. N'hésitez pas à me "
        "contacter.\n\nRépondez STOP.\n\nBien cordialement,"
    ).naturalness
    assert propre > sale
    assert 0 <= sale <= 100


def test_the_analysis_is_deterministic():
    assert analyze_message(BON_MESSAGE).as_dict() == analyze_message(BON_MESSAGE).as_dict()


def test_severities_are_limited_to_the_two_known_values():
    report = analyze_message(
        "Bonjour,\n\nJe me permets de vous écrire 🚀 au sujet de votre "
        "solution innovante.\n\nRépondez STOP.\n\nBien cordialement,"
    )
    assert {f.severity for f in report.findings} <= {SEVERITY_BLOCKING, SEVERITY_WARNING}


def test_an_empty_message_does_not_crash():
    report = analyze_message("")
    assert report.word_count == 0


# --- Objets de message -------------------------------------------------------


@pytest.mark.parametrize(
    ("titre", "attendu"),
    [
        ("Inscriptions 2026-2027", "Vos inscriptions 2026-2027"),
        ("Projet d'établissement 2026", "Votre projet d'établissement 2026"),
        ("Travaux de modernisation", "Vos travaux de modernisation"),
        # Invariable en -s au singulier : « Vos processus » serait fautif.
        ("Processus qualité", "Votre processus qualité"),
    ],
)
def test_the_subject_agrees_in_number(titre, attendu):
    """« Votre inscriptions » saute aux yeux et fait classer le message."""
    from growth_agent.connectors.llm.mock import _subject_from_source

    assert _subject_from_source(titre, "public_website") == attendu


def test_a_press_headline_is_not_turned_into_a_possessive():
    from growth_agent.connectors.llm.mock import _subject_from_source

    assert _subject_from_source("L'institut double ses effectifs", "press_article") == (
        "À propos de : L'institut double ses effectifs"
    )


def test_internal_plural_markers_are_flagged():
    """Régression : « 3 signal(aux) cité(s) » avait atteint un vrai message."""
    report = analyze_message(
        "Bonjour,\n\nJ'ai noté 3 signal(aux) de besoin cité(s).\n\nRépondez STOP.\n\nAwa Njoya"
    )
    assert "jargon_interne" in codes(report)
