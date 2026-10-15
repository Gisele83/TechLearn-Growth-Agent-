#!/usr/bin/env bash
# Démarrage local sous Linux et macOS.
# Lance l'API et l'interface, et s'arrête proprement au Ctrl-C.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "Environnement Python absent. Lancer 'make setup' d'abord."
  exit 1
fi

if [ ! -d apps/web/node_modules ]; then
  echo "Dépendances web absentes. Lancer 'make setup' d'abord."
  exit 1
fi

export PYTHONPATH="$PWD/apps/api/src"
export GA_ENVIRONMENT="${GA_ENVIRONMENT:-local}"

cleanup() {
  echo
  echo "Arrêt…"
  # On arrête les deux processus enfants, pas toute la session.
  kill 0
}
trap cleanup EXIT INT TERM

echo "API       → http://localhost:8000  (documentation : /docs)"
echo "Interface → http://localhost:5173"
echo

.venv/bin/uvicorn growth_agent.main:app --reload --port 8000 &
(cd apps/web && npm run dev) &

wait
