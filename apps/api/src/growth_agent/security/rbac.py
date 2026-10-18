"""Contrôle d'accès par rôle.

Quatre rôles hiérarchisés :

* `lecteur` — consulte, n'écrit rien.
* `operateur` — produit, expédie un message déjà approuvé, relève les
  réponses, enregistre une opposition. **N'approuve pas.**
* `commercial` — tout cela, **et approuve**.
* `admin` — administre la configuration et les oppositions.

Le palier `operateur` sépare **produire** de **valider**. Il est né d'un cas
concret : la personne qui relève la boîte de réponse doit pouvoir reporter
une opposition, ce qui exige d'écrire dans le système. Tant que le seul rôle
capable de le faire était `commercial`, elle recevait du même coup le droit
d'approuver les messages des autres — c'est-à-dire exactement la décision
qu'un second regard est censé couvrir.

Le contrôle est centralisé ici pour qu'aucune route ne puisse inventer sa
propre règle.
"""

from __future__ import annotations

from collections.abc import Callable

from growth_agent.models.enums import Role

#: Rangs espacés de dix : un palier peut être intercalé plus tard sans
#: renuméroter ceux qui existent.
ROLE_RANK: dict[str, int] = {
    Role.LECTEUR: 10,
    Role.OPERATEUR: 20,
    Role.COMMERCIAL: 30,
    Role.ADMIN: 40,
}


class PermissionDenied(Exception):
    """Le rôle courant est insuffisant pour l'action demandée."""

    def __init__(self, required: str, actual: str) -> None:
        super().__init__(f"Rôle '{required}' requis, rôle courant '{actual}'.")
        self.required = required
        self.actual = actual


def has_role(actual_role: str, required_role: str) -> bool:
    return ROLE_RANK.get(actual_role, 0) >= ROLE_RANK.get(required_role, 99)


def require_role(required_role: str) -> Callable[[str], None]:
    """Fabrique un contrôle réutilisable hors du contexte FastAPI."""

    def _check(actual_role: str) -> None:
        if not has_role(actual_role, required_role):
            raise PermissionDenied(required_role, actual_role)

    return _check


# Permissions nommées, pour éviter les littéraux dispersés dans les routes.
CAN_READ = Role.LECTEUR
#: Écrire : rédiger, expédier un message déjà approuvé, reporter une réponse,
#: déclarer un relevé, enregistrer une opposition.
CAN_OPERATE = Role.OPERATEUR
#: Valider un message avant son départ. Volontairement **au-dessus** de
#: `CAN_OPERATE` : c'est la frontière entre produire et engager l'entreprise.
CAN_APPROVE = Role.COMMERCIAL
CAN_ADMINISTER = Role.ADMIN
