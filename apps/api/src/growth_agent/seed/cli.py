"""Installation des données de démonstration en ligne de commande.

Usage : `python -m growth_agent.seed.cli`

Refuse de s'exécuter en production : les comptes créés ont des mots de passe
publiés dans ce dépôt.
"""

from __future__ import annotations

import sys

from growth_agent.config import get_settings
from growth_agent.db.base import Base
from growth_agent.db.session import get_engine, session_scope
from growth_agent.seed.demo import DEMO_USERS, SeedRefused, seed_demo_data


def main() -> int:
    settings = get_settings()
    print(f"Environnement : {settings.environment}")

    # En local, on crée les tables si elles manquent pour que `make seed`
    # fonctionne sans avoir à lancer les migrations d'abord.
    if not settings.is_production:
        Base.metadata.create_all(get_engine())

    try:
        with session_scope() as session:
            created = seed_demo_data(session, settings)
    except SeedRefused as exc:
        print(f"Refusé : {exc}", file=sys.stderr)
        return 1

    print("Données installées :")
    for key, value in created.items():
        print(f"  {key} : {value}")

    print("\nComptes de démonstration (environnement local uniquement) :")
    for email, _name, role, password in DEMO_USERS:
        print(f"  {email:34} {role:11} {password}")
    print("\nCes identifiants sont publics et ne doivent jamais exister ailleurs qu'en local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
