#!/usr/bin/env bash
# Reconstitue une base de démonstration, démarre l'API et l'interface, puis
# capture la page « Performance commerciale » dans docs/captures/.
#
# Rien n'est envoyé : les événements sont écrits en base pour représenter ce
# qu'une campagne réelle aurait produit, et le mode d'envoi réel reste
# désactivé. La base vit dans un fichier temporaire, jamais à côté du dépôt.

set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${GA_DEMO_DB:-/tmp/growth-agent-captures.sqlite3}"
export GA_DATABASE_URL="sqlite+pysqlite:///${BASE}"
export GA_ENVIRONMENT=local
export PYTHONPATH="$PWD/apps/api/src"

rm -f "$BASE"
(cd apps/api && PYTHONPATH=src ../../.venv/bin/alembic upgrade head)
.venv/bin/python -m growth_agent.seed.cli
.venv/bin/python scripts/demo-history.py

.venv/bin/uvicorn growth_agent.main:app --port 8000 &
API=$!
(cd apps/web && npm run dev) &
WEB=$!
trap 'kill $API $WEB 2>/dev/null || true; rm -f "$BASE"' EXIT

# Attendre que les deux répondent plutôt que de dormir au hasard.
for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1 \
     && curl -fsS http://localhost:5173/ >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

mkdir -p docs/captures
(cd apps/web && CHROMIUM_PATH="${CHROMIUM_PATH:-}" node ../../scripts/captures.mjs)
