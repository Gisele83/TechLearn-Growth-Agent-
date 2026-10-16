"""Les mots par lesquels quelqu'un demande qu'on le laisse tranquille.

Une seule liste, partagée par le rédacteur (qui vérifie qu'un message offre
un moyen de s'opposer) et par l'analyste de réponses (qui reconnaît une
opposition). Deux listes auraient divergé, et c'est toujours celle qui
reconnaît le moins qui aurait servi à écarter quelqu'un.

La reconnaissance est **déterministe, sans modèle**. Une opposition ratée
parce qu'une inférence a hésité est une infraction ; mieux vaut une liste
franche, élargie à mesure qu'on rencontre des tournures réelles.

Le texte proposé aux messages dépend, lui, d'un fait vérifiable : une réponse
atteint-elle quelqu'un ? Promettre « répondez STOP » depuis une adresse qui
ne reçoit pas est pire que ne rien promettre — la personne croit s'être
opposée, et on la recontacte.
"""

from __future__ import annotations

import unicodedata

#: Formulations reconnues comme une demande d'opposition. Comparées sur un
#: texte mis en minuscules et débarrassé de ses accents, pour que
#: « désinscription », « desinscription » et « DÉSINSCRIPTION » se valent.
OPT_OUT_MARKERS: tuple[str, ...] = (
    # Le mot-clé canonique, celui qu'on annonce dans les messages.
    "stop",
    # Désinscription, sous ses formes
    "desinscri",
    "desabonn",
    "unsubscribe",
    "opt out",
    "opt-out",
    # Demandes directes
    "ne plus me contacter",
    "ne plus me recontacter",
    "ne plus recevoir",
    "ne plus etre contacte",
    "ne souhaite plus",
    "ne veux plus",
    "plus de messages",
    "plus de sollicitation",
    "arretez de m",
    "cessez de m",
    "laissez-moi tranquille",
    "laissez moi tranquille",
    # Retrait et suppression
    "retirez-moi",
    "retirez moi",
    "retirer mon adresse",
    "supprimez mon adresse",
    "supprimez mes donnees",
    "effacez mes donnees",
    "remove me",
    "take me off",
    # Vocabulaire juridique
    "opposition",
    "droit d'opposition",
    "je m'oppose",
    "je mopppose",
)

#: Mention d'opposition exigée dans un message sortant. Reconnue par les
#: mêmes marqueurs, plus les tournures qui renvoient au lien.
LINK_MARKERS: tuple[str, ...] = (
    "lien de desinscription",
    "vous desinscrire",
    "me desinscrire",
    "ne plus recevoir",
)


def normalize(text: str) -> str:
    """Minuscules, sans accents. Un accent manquant ne doit pas faire rater
    une opposition."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def is_opt_out(text: str) -> bool:
    """Ce texte demande-t-il à ne plus être contacté ?"""
    normalized = normalize(text)
    return any(marker in normalized for marker in OPT_OUT_MARKERS)


def mentions_opt_out(body: str) -> bool:
    """Ce message offre-t-il un moyen de s'opposer ?"""
    normalized = normalize(body)
    return any(marker in normalized for marker in OPT_OUT_MARKERS + LINK_MARKERS)


def closing_line(*, replies_are_monitored: bool) -> str:
    """Dernière ligne du message, celle qui ouvre le droit d'opposition.

    Deux formulations, parce qu'il y a deux situations réelles — et qu'une
    seule des deux peut être tenue à un moment donné.
    """
    if replies_are_monitored:
        return "Si ce n'est pas votre sujet, répondez STOP et je ne vous recontacte pas."
    return (
        "Si ce n'est pas votre sujet, le lien de désinscription en bas de ce "
        "message vous retire de nos envois."
    )
