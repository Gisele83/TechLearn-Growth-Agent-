"""Codes HTTP utilisés par l'API.

`HTTP_422_UNPROCESSABLE_ENTITY` a été renommé dans Starlette ; référencer la
valeur numérique évite de dépendre du nom, qui change d'une version à l'autre.
"""

from __future__ import annotations

#: Entité bien formée mais sémantiquement invalide (RFC 9110 § 15.5.21).
UNPROCESSABLE_CONTENT = 422
