"""État du schéma : la base est-elle à la révision que ce code attend ?

Sert à la sonde de disponibilité. Un conteneur démarré avant la fin des
migrations répond à tout et se trompe sur la moitié des requêtes : il vaut
mieux qu'il se déclare indisponible que qu'il serve des erreurs.

La tête attendue est lue dans les fichiers de migration du dépôt, pas
codée en dur : elle suit le code, ce qui est exactement la question posée —
« ce binaire et cette base sont-ils de la même génération ? »
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

#: `migrations/versions/` à côté du paquet installé, ou à la racine de l'API.
_VERSIONS_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "migrations" / "versions",
    Path(__file__).resolve().parents[2] / "migrations" / "versions",
)

_REVISION = re.compile(r"^revision\s*(?::\s*str\s*)?=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_DOWN_REVISION = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


@dataclass(frozen=True)
class RevisionState:
    current: str | None
    expected: str | None
    up_to_date: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "current": self.current,
            "expected": self.expected,
            "up_to_date": self.up_to_date,
        }


@lru_cache(maxsize=1)
def expected_head() -> str | None:
    """Révision de tête d'après les fichiers du dépôt.

    `None` quand les migrations ne sont pas distribuées avec le code — la
    sonde ne doit pas déclarer l'instance indisponible pour une information
    qu'elle n'a pas.
    """
    directory = next((path for path in _VERSIONS_CANDIDATES if path.is_dir()), None)
    if directory is None:
        return None

    revisions: set[str] = set()
    parents: set[str] = set()
    for script in directory.glob("*.py"):
        content = script.read_text(encoding="utf-8")
        found = _REVISION.search(content)
        if not found:
            continue
        revisions.add(found.group(1))
        previous = _DOWN_REVISION.search(content)
        if previous:
            parents.add(previous.group(1))

    heads = revisions - parents
    # Plusieurs têtes signifient des branches de migration non fusionnées :
    # on ne devine pas laquelle est la bonne.
    return heads.pop() if len(heads) == 1 else None


def current_revision(session: Session) -> str | None:
    try:
        return session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except Exception:
        # Table absente : base créée par `create_all` (tests) ou jamais migrée.
        return None


def revision_state(session: Session) -> RevisionState:
    expected = expected_head()
    current = current_revision(session)
    if expected is None or current is None:
        # Sans point de comparaison, on ne bloque pas : une sonde qui refuse
        # le trafic faute d'information est une panne qu'on a fabriquée.
        return RevisionState(current=current, expected=expected, up_to_date=True)
    return RevisionState(current=current, expected=expected, up_to_date=current == expected)
