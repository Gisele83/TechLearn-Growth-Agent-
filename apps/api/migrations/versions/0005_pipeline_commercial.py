"""Opportunités et prochaines actions

Deux tables, pour une question que le modèle ne savait pas poser : **qui
doit faire quoi, sur quel dossier, cette semaine ?**

* `opportunities` — le dossier commercial, avec porteur, montant, date de
  clôture envisagée et motif de perte. `prospect_id` est en `SET NULL` et
  non en `CASCADE` : la purge anonymise le prospect, et le dossier doit
  survivre pour que les indicateurs historiques tiennent. Le nom de
  l'organisation y est dupliqué pour la même raison — c'est une raison
  sociale, pas une donnée personnelle.

* `next_actions` — l'engagement daté et attribué. Les tâches créées
  jusqu'ici partaient dans le CRM et n'en revenaient jamais : le tableau de
  bord ne pouvait pas les voir. `ix_next_action_owner` est l'index de la
  question du lundi matin.

Aucune reprise de données : ces tables n'existaient pas, et rien dans
l'existant ne permet de deviner un porteur ou une échéance.

Revision ID: 0005_pipeline_commercial
Revises: 0004_crm_idempotency
Create Date: 2026-10-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

import growth_agent.db.types


revision = '0005_pipeline_commercial'
down_revision = '0004_crm_idempotency'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('opportunities',
    sa.Column('product_id', growth_agent.db.types.GUID(), nullable=False),
    sa.Column('campaign_id', growth_agent.db.types.GUID(), nullable=True),
    sa.Column('prospect_id', growth_agent.db.types.GUID(), nullable=True),
    sa.Column('organization_name', sa.String(length=256), nullable=False),
    sa.Column('stage', sa.String(length=24), nullable=False),
    sa.Column('owner_email', sa.String(length=256), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('expected_close_on', growth_agent.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('closed_at', growth_agent.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('loss_reason', sa.String(length=48), nullable=False),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('id', growth_agent.db.types.GUID(), nullable=False),
    sa.Column('created_at', growth_agent.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', growth_agent.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospect_candidates.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.create_index('ix_opportunity_owner', ['owner_email', 'stage'], unique=False)
        batch_op.create_index('ix_opportunity_prospect', ['prospect_id'], unique=False)
        batch_op.create_index('ix_opportunity_stage', ['product_id', 'stage'], unique=False)

    op.create_table('next_actions',
    sa.Column('opportunity_id', growth_agent.db.types.GUID(), nullable=True),
    sa.Column('product_id', growth_agent.db.types.GUID(), nullable=False),
    sa.Column('prospect_id', growth_agent.db.types.GUID(), nullable=True),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=192), nullable=False),
    sa.Column('owner_email', sa.String(length=256), nullable=False),
    sa.Column('due_at', growth_agent.db.types.UTCDateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('completed_at', growth_agent.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('outcome', sa.Text(), nullable=False),
    sa.Column('crm_remote_id', sa.String(length=128), nullable=False),
    sa.Column('payload', growth_agent.db.types.JSONDict(), nullable=False),
    sa.Column('id', growth_agent.db.types.GUID(), nullable=False),
    sa.Column('created_at', growth_agent.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', growth_agent.db.types.UTCDateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['opportunity_id'], ['opportunities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['prospect_id'], ['prospect_candidates.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('next_actions', schema=None) as batch_op:
        batch_op.create_index('ix_next_action_due', ['status', 'due_at'], unique=False)
        batch_op.create_index('ix_next_action_owner', ['owner_email', 'status', 'due_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('next_actions', schema=None) as batch_op:
        batch_op.drop_index('ix_next_action_owner')
        batch_op.drop_index('ix_next_action_due')

    op.drop_table('next_actions')
    with op.batch_alter_table('opportunities', schema=None) as batch_op:
        batch_op.drop_index('ix_opportunity_stage')
        batch_op.drop_index('ix_opportunity_prospect')
        batch_op.drop_index('ix_opportunity_owner')

    op.drop_table('opportunities')
