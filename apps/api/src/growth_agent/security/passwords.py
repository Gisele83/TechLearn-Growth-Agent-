"""Hachage de mots de passe (PBKDF2-HMAC-SHA256, bibliothèque standard).

Aucune dépendance native : l'application s'installe et se teste partout à
l'identique. Le format stocké est `pbkdf2_sha256$<itérations>$<sel>$<clé>`, ce
qui permet d'augmenter le nombre d'itérations plus tard sans invalider les
condensats existants.
"""

from __future__ import annotations

import hashlib
import hmac
import os

PBKDF2_ROUNDS = 390_000
PREFIX = "pbkdf2_sha256"


def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    if not password:
        raise ValueError("Le mot de passe ne peut pas être vide.")
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"{PREFIX}${rounds}${salt.hex()}${derived.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Comparaison à temps constant. Un condensat illisible renvoie False."""
    if not password or not password_hash:
        return False
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != PREFIX:
        return False
    try:
        rounds = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = parts[3]
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(derived.hex(), expected)


def needs_rehash(password_hash: str, *, rounds: int = PBKDF2_ROUNDS) -> bool:
    """Indique qu'un condensat a été produit avec un coût désormais insuffisant."""
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[0] != PREFIX:
        return True
    try:
        return int(parts[1]) < rounds
    except ValueError:
        return True
