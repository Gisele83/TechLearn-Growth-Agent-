"""Contrôle d'accès par rôle.

Trois rôles hiérarchisés. `lecteur` consulte, `commercial` produit et approuve
les messages, `admin` administre la configuration et les oppositions. Le
contrôle est centralisé ici pour qu'aucune route ne puisse inventer sa propre
règle.
"""

from __future__ import annotations

from collections.abc import Callable

from growth_agent.models.enums import Role

ROLE_RANK: dict[str, int] = {
    Role.LECTEUR: 10,
    Role.COMMERCIAL: 20,
    Role.ADMIN: 30,
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
CAN_OPERATE = Role.COMMERCIAL
CAN_APPROVE = Role.COMMERCIAL
CAN_ADMINISTER = Role.ADMIN
