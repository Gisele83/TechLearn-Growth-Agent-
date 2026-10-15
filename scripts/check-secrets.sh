#!/usr/bin/env bash
# Recherche de secrets committés par inadvertance.
#
# Volontairement simple et sans dépendance : un filet de sécurité de plus, pas
# un remplacement d'un scanner dédié. Branché en pre-commit et dans `make check`.

set -euo pipefail

cd "$(dirname "$0")/.."

FAILED=0

# Motifs de clés courantes. On cherche la FORME d'un secret, pas un mot-clé :
# chercher « password » produirait trop de faux positifs dans la documentation.
PATTERNS=(
  'sk-[A-Za-z0-9]{20,}'
  'pat-[A-Za-z0-9-]{20,}'
  'ghp_[A-Za-z0-9]{30,}'
  'AIza[A-Za-z0-9_-]{30,}'
  'xox[baprs]-[A-Za-z0-9-]{10,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
  'postgres(ql)?://[^:/@ ]+:[^@ ]+@'
  'AWS_SECRET_ACCESS_KEY[[:space:]]*[:=][[:space:]]*[A-Za-z0-9/+=]{20,}'
)

# Fichiers suivis par git uniquement : ce qui n'est pas committé n'est pas le
# sujet de ce contrôle.
FILES=$(git ls-files | grep -vE '^(docs/|.*\.lock$|.*package-lock\.json$|pnpm-lock\.yaml$)' || true)

if [ -z "$FILES" ]; then
  echo "Aucun fichier suivi à analyser."
  exit 0
fi

for pattern in "${PATTERNS[@]}"; do
  # shellcheck disable=SC2086
  if MATCHES=$(grep -InE "$pattern" $FILES 2>/dev/null); then
    echo "Motif de secret détecté ($pattern) :"
    echo "$MATCHES" | sed 's/^/  /'
    FAILED=1
  fi
done

# Un fichier .env ne doit jamais être suivi. .env.example, si.
if git ls-files | grep -qE '(^|/)\.env$'; then
  echo "Le fichier .env est suivi par git : le retirer avec 'git rm --cached .env'."
  FAILED=1
fi

# Les variables sensibles de .env.example doivent rester vides.
if [ -f .env.example ]; then
  if BAD=$(grep -nE '^(GA_[A-Z_]*(TOKEN|KEY|PASSWORD|SECRET))=.+$' .env.example); then
    echo "Valeurs renseignées dans .env.example (elles doivent rester vides) :"
    echo "$BAD" | sed 's/^/  /'
    FAILED=1
  fi
fi

if [ "$FAILED" -eq 0 ]; then
  echo "Aucun secret détecté dans les fichiers suivis."
fi
exit "$FAILED"
