# Raccourcis de développement. Tout tourne en local et hors ligne.

SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
API := apps/api
WEB := apps/web

.DEFAULT_GOAL := help

.PHONY: help
help: ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Crée l'environnement Python et installe les dépendances
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(API)[dev]"
	cd $(WEB) && npm install

.PHONY: migrate
migrate: ## Applique les migrations
	cd $(API) && PYTHONPATH=src ../../$(VENV)/bin/alembic upgrade head

.PHONY: seed
seed: ## Installe les données de démonstration (refusé en production)
	PYTHONPATH=$(API)/src $(PY) -m growth_agent.seed.cli

.PHONY: api
api: ## Démarre l'API sur http://localhost:8000
	PYTHONPATH=$(API)/src $(VENV)/bin/uvicorn growth_agent.main:app --reload --port 8000

.PHONY: web
web: ## Démarre l'interface sur http://localhost:5173
	cd $(WEB) && npm run dev

.PHONY: test
test: test-api test-web ## Lance toute la suite de tests

.PHONY: test-api
test-api: ## Tests de l'API
	cd $(API) && ../../$(VENV)/bin/python -m pytest -q

.PHONY: test-web
test-web: ## Tests de l'interface
	cd $(WEB) && npm run test

.PHONY: lint
lint: ## Vérifie le style et le typage
	cd $(API) && ../../$(VENV)/bin/ruff check src tests
	cd $(API) && ../../$(VENV)/bin/ruff format --check src tests
	cd $(WEB) && npm run typecheck

.PHONY: format
format: ## Reformate le code
	cd $(API) && ../../$(VENV)/bin/ruff format src tests
	cd $(API) && ../../$(VENV)/bin/ruff check --fix src tests

.PHONY: secrets
secrets: ## Recherche un secret qui aurait été committé
	@scripts/check-secrets.sh

.PHONY: up
up: ## Démarre la pile locale (PostgreSQL, Redis, API, interface)
	docker compose up --build

.PHONY: down
down: ## Arrête la pile locale en CONSERVANT les données
	docker compose down

.PHONY: check
check: lint test secrets ## Contrôle complet avant commit
	@echo "Tous les contrôles sont passés."
