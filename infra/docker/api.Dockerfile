# Image de développement local pour l'API.
# L'image de production est décrite dans docs/09-dossier-deploiement.md ; elle
# n'est ni construite ni publiée depuis ce dépôt.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
RUN pip install --upgrade pip && pip install -e ./apps/api

COPY apps/api ./apps/api

# L'application ne tourne pas en root, même en local : la même image sert de
# base à l'image de production.
RUN useradd --create-home --uid 10001 growth \
    && chown -R growth:growth /app
USER growth

ENV PYTHONPATH=/app/apps/api/src
EXPOSE 8000

CMD ["uvicorn", "growth_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
