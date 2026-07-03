# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Add message.cypher + message.truncated + query_preset.hops + query_preset.cypher_timeout_ms.

Closes the three plan2 follow-ups:
- message.cypher/truncated persist the cypher/hybrid retrieval audit on each
  assistant turn (so reopening a conversation still shows what ran and whether
  the row cap bit).
- query_preset.hops/cypher_timeout_ms make the two method-specific knobs
  preset-persistable (hybrid -> hops, cypher -> cypher_timeout_ms).

All four columns are nullable except ``truncated`` (NOT NULL DEFAULT 0 so old
assistant rows read as not-truncated).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column("cypher", sa.Text(), nullable=True),
    )
    op.add_column(
        "message",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "query_preset",
        sa.Column("hops", sa.Integer(), nullable=True),
    )
    op.add_column(
        "query_preset",
        sa.Column("cypher_timeout_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("query_preset", "cypher_timeout_ms")
    op.drop_column("query_preset", "hops")
    op.drop_column("message", "truncated")
    op.drop_column("message", "cypher")
