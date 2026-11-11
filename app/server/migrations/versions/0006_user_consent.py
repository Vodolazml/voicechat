"""accepted edition of the privacy policy, terms and consent

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('consent_version', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('consent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('consent_at')
        batch_op.drop_column('consent_version')
