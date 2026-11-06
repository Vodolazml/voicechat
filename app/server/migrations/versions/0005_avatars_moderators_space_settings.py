"""avatars, space moderators and space settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('avatar_version', sa.Integer(), nullable=False, server_default='0'))
    with op.batch_alter_table('spaces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('avatar', sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column('avatar_version', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('max_share_quality', sa.String(length=16), nullable=False,
                                      server_default='1440p60'))
        batch_op.add_column(sa.Column('camera_allowed', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        'space_moderators',
        sa.Column('space_id', sa.Integer(), sa.ForeignKey('spaces.id'), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table('space_moderators')
    with op.batch_alter_table('spaces', schema=None) as batch_op:
        batch_op.drop_column('camera_allowed')
        batch_op.drop_column('max_share_quality')
        batch_op.drop_column('avatar_version')
        batch_op.drop_column('avatar')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('avatar_version')
        batch_op.drop_column('avatar')
