"""users, job_runs, project ownership, encrypted youtube secrets

Revision ID: c3f8a91e6b02
Revises: 9d1f2c4b8a21
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f8a91e6b02'
down_revision: Union[str, Sequence[str], None] = '9d1f2c4b8a21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    op.add_column('projects', sa.Column('user_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_projects_user_id', 'projects', 'users', ['user_id'], ['id'])

    op.create_table(
        'job_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_type', sa.String(), nullable=False),
        sa.Column('celery_task_id', sa.String(), nullable=True),
        sa.Column('project_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', name='jobstatus'), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_runs_celery_task_id', 'job_runs', ['celery_task_id'])

    # NOTE on YouTubeConfig columns (client_id/client_secret/refresh_token):
    # their DB type stays TEXT either way (EncryptedText's `impl` is Text),
    # so no column-type ALTER is needed here. But any pre-existing plaintext
    # rows written before this migration will fail to decrypt (crypto.py
    # raises InvalidToken rather than silently returning garbage). This
    # scaffold has no code path that ever wrote real values into those
    # columns yet, so in practice there's nothing to migrate -- flagging
    # this explicitly in case that's no longer true by the time this runs.


def downgrade() -> None:
    op.drop_index('ix_job_runs_celery_task_id', table_name='job_runs')
    op.drop_table('job_runs')
    op.drop_constraint('fk_projects_user_id', 'projects', type_='foreignkey')
    op.drop_column('projects', 'user_id')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')