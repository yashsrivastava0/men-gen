"""run meta: a JSON column for what the standalone app records about a run (input, size class, spend)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11 15:00:00+00:00

Append-only: never edit this file once it has been applied anywhere; add a new revision instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rsch_runs",
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("rsch_runs", "meta")
