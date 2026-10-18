"""Politique de rétention : ce qui est purgé, anonymisé, ou jamais touché.

Trois principes, par ordre de priorité. En cas de conflit, le premier l'emporte.

**1. Une opposition survit à tout.** `suppression_entries` n'est jamais purgée.
L'adresse d'une personne qui a demandé à ne plus être contactée est la donnée
minimale nécessaire pour honorer ce refus : l'effacer reviendrait à la
recontacter six mois plus tard. Le RGPD prévoit explicitement ce cas
(art. 17.3.b et art. 21) : la conservation est ici au service de la personne,
pas contre elle.

**2. Un blocage légal gèle l'enregistrement.** `legal_hold` suspend toute
purge, quelle que soit l'échéance. Litige, obligation comptable, réquisition :
la levée est manuelle et journalisée.

**3. L'historique des indicateurs ne se réécrit pas.** Un prospect ayant une
histoire commerciale est **anonymisé**, pas supprimé : ses champs personnels
sont effacés, sa ligne et ses événements subsistent. Supprimer la ligne
ferait disparaître ses événements en cascade et changerait rétroactivement
les chiffres du trimestre passé — un système dont les indicateurs bougent
tout seuls n'est plus un système de mesure.

Un prospect sans aucune histoire commerciale, lui, est supprimé : il n'y a
rien à préserver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from growth_agent.config import Settings

#: Tables que la purge ne touche sous aucun prétexte. Énumérées ici pour être
#: vérifiables par un test, et non laissées à la vigilance du lecteur.
PROTECTED_TABLES: frozenset[str] = frozenset(
    {
        # Les oppositions : voir principe 1.
        "suppression_entries",
        # Le référentiel produit et la configuration : ce ne sont pas des
        # données personnelles et leur disparition casserait l'application.
        "products",
        "campaigns",
        "ideal_customer_profiles",
        "message_templates",
        "integration_configs",
        "users",
        # La trace des purges elles-mêmes.
        "purge_runs",
    }
)


@dataclass(frozen=True, slots=True)
class RetentionRule:
    """Règle de conservation applicable à une classe de donnée."""

    key: str
    label: str
    table: str
    retention_days: int
    action: str  # "anonymize" | "delete" | "scrub"
    rationale: str

    def cutoff(self, now: datetime) -> datetime:
        """Date avant laquelle un enregistrement est échu."""
        return now - timedelta(days=self.retention_days)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    rules: tuple[RetentionRule, ...] = field(default_factory=tuple)

    def rule(self, key: str) -> RetentionRule:
        for item in self.rules:
            if item.key == key:
                return item
        raise KeyError(f"Règle de rétention inconnue : {key}")

    def __iter__(self):
        return iter(self.rules)


def build_policy(settings: Settings) -> RetentionPolicy:
    """Construit la politique à partir de la configuration."""
    return RetentionPolicy(
        rules=(
            RetentionRule(
                key="prospects",
                label="Prospects non convertis",
                table="prospect_candidates",
                retention_days=settings.data_retention_days,
                action="anonymize",
                rationale=(
                    "Les coordonnées professionnelles collectées pour une prise de "
                    "contact n'ont plus de finalité une fois la durée de conservation "
                    "écoulée sans conversion. L'organisation et son historique "
                    "d'événements subsistent pour que les indicateurs restent stables."
                ),
            ),
            RetentionRule(
                key="drafts",
                label="Contenu des messages",
                table="message_drafts",
                retention_days=settings.draft_retention_days,
                action="scrub",
                rationale=(
                    "Le corps d'un message contient le nom et la fonction du "
                    "destinataire. Passé six mois, seuls le statut et l'horodatage "
                    "restent utiles ; le texte est effacé."
                ),
            ),
            RetentionRule(
                key="audit",
                label="Journal d'audit",
                table="audit_events",
                retention_days=settings.audit_retention_days,
                action="delete",
                rationale=(
                    "Conservé trois ans au titre de la traçabilité des décisions "
                    "commerciales. Le journal ne contient ni secret ni donnée "
                    "personnelle en clair : le masquage est appliqué à l'écriture."
                ),
            ),
            RetentionRule(
                key="pilot_events",
                label="Événements de pilote",
                table="product_pilot_events",
                retention_days=settings.pilot_event_retention_days,
                action="delete",
                rationale=(
                    "Statuts commerciaux lus chez Maintrix et EduCore. Conservés "
                    "cinq ans comme éléments de la relation client, puis supprimés. "
                    "Leur suppression côté Growth Agent n'affecte pas les "
                    "plateformes d'origine, qui restent seules maîtresses de leurs "
                    "données."
                ),
            ),
        )
    )


#: Champs personnels effacés lors de l'anonymisation d'un prospect.
#: La valeur de remplacement est explicite : un champ vide se confond avec une
#: donnée jamais collectée, ce qui empêcherait de distinguer les deux cas.
ANONYMIZED_PROSPECT_FIELDS: dict[str, object] = {
    "contact_full_name": None,
    "contact_email": None,
    "contact_phone": None,
    "contact_role": None,
    "notes": "[anonymisé]",
}

#: Champs effacés sur les brouillons rattachés à un prospect anonymisé.
ANONYMIZED_DRAFT_FIELDS: dict[str, object] = {
    "recipient_email": None,
    "subject": "[anonymisé]",
    "body": "[anonymisé]",
    "variables_used": dict,
}

#: Sur un dossier commercial, les notes sont écrites à la main et racontent
#: souvent un échange avec une personne nommée. Le nom de l'organisation, le
#: montant et le motif de perte sont conservés : ce sont des faits
#: d'entreprise, et les retirer viderait les indicateurs historiques.
ANONYMIZED_OPPORTUNITY_FIELDS: dict[str, object] = {
    "notes": "[anonymisé]",
}

#: Sur une action, le titre et le compte rendu sont saisis librement et
#: peuvent nommer quelqu'un. Le porteur, lui, est un salarié : il relève d'un
#: autre régime que les données de prospection, et reste en place.
ANONYMIZED_ACTION_FIELDS: dict[str, object] = {
    "title": "[anonymisé]",
    "outcome": "[anonymisé]",
    "payload": dict,
}

#: Sur une source, l'extrait peut citer une personne. L'URL et la date sont
#: conservées : ce sont elles qui prouvent la provenance, et leur disparition
#: rendrait impossible de justifier a posteriori pourquoi ce prospect avait
#: été retenu.
ANONYMIZED_SOURCE_FIELDS: dict[str, object] = {
    "excerpt": "[anonymisé]",
}


def default_now() -> datetime:
    return datetime.now(UTC)
