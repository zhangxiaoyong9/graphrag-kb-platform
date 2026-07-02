# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""Add knowledge_base.llm_fallback_profile_ids (nullable Text).

T12 of the Native LLM Provider Layer (Phase 2). The column holds a
JSON-encoded ordered list of fallback LLM provider-profile ids; the failover
order at query time is ``[llm_profile_id] + json.loads(this or "[]")``.
Existing KBs have NULL (treated as no fallback) and behave exactly as today —
the column is nullable with no server default, matching ``settings_json``'s
convention. Consumed by the cross-profile failover gateway (T14).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base",
        sa.Column("llm_fallback_profile_ids", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_base", "llm_fallback_profile_ids")
