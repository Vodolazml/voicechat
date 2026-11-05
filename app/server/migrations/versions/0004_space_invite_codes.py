"""space invite codes

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 21:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('spaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('invite_code', sa.String(length=16), nullable=True))
        batch_op.create_index(batch_op.f('ix_spaces_invite_code'), ['invite_code'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('spaces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_spaces_invite_code'))
        batch_op.drop_column('invite_code')
