"""youtube config table

Revision ID: 9d1f2c4b8a21
Revises: 7a8c2862e1c5
Create Date: 2026-08-21 15:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d1f2c4b8a21'
down_revision: Union[str, Sequence[str], None] = '7a8c2862e1c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'youtube_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('channel_id', sa.String(), nullable=True),
        sa.Column('channel_name', sa.String(), nullable=True),
        sa.Column('default_privacy_status', sa.String(), nullable=True),
        sa.Column('default_category_id', sa.String(), nullable=True),
        sa.Column('default_language', sa.String(), nullable=True),
        sa.Column('default_tags', sa.JSON(), nullable=True),
        sa.Column('made_for_kids', sa.Boolean(), nullable=True),
        sa.Column('auto_publish_enabled', sa.Boolean(), nullable=True),
        sa.Column('upload_description_footer', sa.Text(), nullable=True),
        sa.Column('client_id', sa.Text(), nullable=True),
        sa.Column('client_secret', sa.Text(), nullable=True),
        sa.Column('refresh_token', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('youtube_config')
