"""call log turn and event ids

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10 00:00:00+00:00

Additive nullable turn_id and event_id on compass_call_log.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("compass_call_log", sa.Column("turn_id", sa.Text(), nullable=True))
    op.add_column("compass_call_log", sa.Column("event_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("compass_call_log", "event_id")
    op.drop_column("compass_call_log", "turn_id")

