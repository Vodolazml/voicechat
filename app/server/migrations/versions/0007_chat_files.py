"""files in chat

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-13 19:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('channels.id'), nullable=False),
        sa.Column('uploader_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_attachments_channel_id', 'attachments', ['channel_id'])
    op.create_index('ix_attachments_uploader_id', 'attachments', ['uploader_id'])
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('attachment_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_messages_attachment_id', 'attachments', ['attachment_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_constraint('fk_messages_attachment_id', type_='foreignkey')
        batch_op.drop_column('attachment_id')
    op.drop_index('ix_attachments_uploader_id', 'attachments')
    op.drop_index('ix_attachments_channel_id', 'attachments')
    op.drop_table('attachments')
