"""telegram links

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19 17:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'telegram_links',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('chat_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('tg_username', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('notify_joins', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('telegram_links')
