"""Idempotence CRM tenue localement

`crm_mappings` devient la mémoire d'idempotence des écritures CRM, et plus
seulement une table de correspondance.

* `idempotency_key` — la clé de l'écriture qui a créé l'objet distant, avec un
  index unique partiel. C'est lui qui rend l'idempotence réelle : deux
  écritures portant la même clé ne peuvent pas produire deux objets. Partiel,
  parce qu'une correspondance importée d'un CRM existant n'a pas de clé.

* `uq_crm_mapping_local` est retiré. Il imposait une seule correspondance par
  (fournisseur, type, objet local), ce qui est juste pour une entreprise ou un
  contact mais faux pour une tâche : un prospect en reçoit plusieurs, une
  relance puis une démonstration. La deuxième échouait en
  `UNIQUE constraint failed`. L'unicité qui compte est portée par la clé
  d'idempotence ; la recherche par objet local garde un index, non unique.

Revision ID: 0004_crm_idempotency
Revises: 0003_unsubscribe_and_reservation
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_crm_idempotency"
down_revision = "0003_unsubscribe_and_reservation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_mappings",
        sa.Column("idempotency_key", sa.String(length=64), nullable=False, server_default=""),
    )
    # SQLite ne sait pas modifier une colonne existante ; il ne sert qu'aux
    # tests, et un défaut inerte n'y change rien.
    if op.get_bind().dialect.name != "sqlite":
        op.alter_column("crm_mappings", "idempotency_key", server_default=None)

    with op.batch_alter_table("crm_mappings", schema=None) as batch_op:
        batch_op.drop_constraint("uq_crm_mapping_local", type_="unique")

    op.create_index(
        "ix_crm_mapping_local",
        "crm_mappings",
        ["crm_provider", "object_type", "local_id"],
        unique=False,
    )
    op.create_index(
        "uq_crm_mapping_idempotency",
        "crm_mappings",
        ["crm_provider", "object_type", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key <> ''"),
        sqlite_where=sa.text("idempotency_key <> ''"),
    )


def downgrade() -> None:
    # Le retour n'est possible que si aucun objet local ne porte plusieurs
    # correspondances du même type — sans quoi la contrainte d'origine ne peut
    # pas être rétablie. On supprime alors les correspondances surnuméraires,
    # en gardant la plus ancienne : c'est la seule issue, et elle est
    # explicite plutôt que silencieuse.
    op.drop_index("uq_crm_mapping_idempotency", table_name="crm_mappings")
    op.drop_index("ix_crm_mapping_local", table_name="crm_mappings")
    op.execute(
        """
        DELETE FROM crm_mappings
         WHERE EXISTS (
               SELECT 1
                 FROM crm_mappings AS plus_ancienne
                WHERE plus_ancienne.crm_provider = crm_mappings.crm_provider
                  AND plus_ancienne.object_type = crm_mappings.object_type
                  AND plus_ancienne.local_id = crm_mappings.local_id
                  AND plus_ancienne.id < crm_mappings.id
             )
        """
    )
    with op.batch_alter_table("crm_mappings", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_crm_mapping_local", ["crm_provider", "object_type", "local_id"]
        )
    op.drop_column("crm_mappings", "idempotency_key")
