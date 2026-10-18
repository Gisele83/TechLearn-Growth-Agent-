"""Origine d'un événement : constaté ou déclaré

« 12 démonstrations tenues » ne vaut pas la même chose selon que le système
les a constatées ou qu'une personne les a saisies. La seconde dépend de la
rigueur de quelqu'un, et son absence ne prouve rien. Un tableau de bord qui
mélange les deux ment par omission.

**Reprise de l'existant.** L'origine est déduite du type d'événement, et
cette déduction est exacte pour tout ce qui a été écrit jusqu'ici :

* `sent`, `send_blocked`, `delivered`, `bounced`, `opened` — le système les
  a constatés, par le fournisseur ou par ses propres garde-fous ;
* tout le reste — réponse, démonstration, pilote, décision — a été saisi par
  une personne. Aucune intégration de réception n'existe : une réponse de
  prospect ne peut pas avoir été observée.

Le défaut de colonne est `declared`, et c'est volontaire : supposer qu'un
événement a été constaté quand on n'en sait rien surestimerait la fiabilité
des chiffres.

Revision ID: 0006_event_source
Revises: 0005_pipeline_commercial
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_event_source"
down_revision = "0005_pipeline_commercial"
branch_labels = None
depends_on = None

#: Types d'événements que le système constate lui-même.
OBSERVED_TYPES = ("sent", "send_blocked", "delivered", "bounced", "opened")


def upgrade() -> None:
    op.add_column(
        "outreach_events",
        sa.Column(
            "event_source", sa.String(length=16), nullable=False, server_default="declared"
        ),
    )
    # Le défaut est retiré après la reprise : l'application fournit toujours
    # la valeur, et le laisser masquerait un oubli côté code. SQLite ne sait
    # pas modifier une colonne existante ; il ne sert qu'aux tests.
    op.execute(
        sa.text(
            "UPDATE outreach_events SET event_source = 'observed' "
            "WHERE event_type IN :types"
        ).bindparams(sa.bindparam("types", value=OBSERVED_TYPES, expanding=True))
    )
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("outreach_events", "event_source", server_default=None)

    op.create_index(
        "ix_event_source",
        "outreach_events",
        ["product_id", "event_source", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_event_source", table_name="outreach_events")
    op.drop_column("outreach_events", "event_source")
