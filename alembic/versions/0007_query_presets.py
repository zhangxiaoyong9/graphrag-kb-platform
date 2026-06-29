# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License.
"""query_preset: global, cross-KB retrieval-tuning preset library (A3).

NOTE: the built-in seed list is DUPLICATED in ``Repository._seed_builtin_presets``
(in-memory test DBs created via Base.metadata.create_all bypass Alembic). Keep
the two lists in sync when editing.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-30
"""
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_preset",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("description", sa.String, nullable=False, server_default=""),
        sa.Column("method", sa.String, nullable=False),
        sa.Column("community_level", sa.Integer, nullable=True),
        sa.Column("response_type", sa.String, nullable=True),
        sa.Column("top_k", sa.Integer, nullable=True),
        sa.Column("temperature", sa.Float, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    now = datetime.now()
    op.bulk_insert(
        sa.table(
            "query_preset",
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("method", sa.String),
            sa.column("community_level", sa.Integer),
            sa.column("response_type", sa.String),
            sa.column("temperature", sa.Float),
            sa.column("is_builtin", sa.Boolean),
            sa.column("created_at", sa.DateTime),
        ),
        [
            {
                "name": "默认",
                "description": "graphrag 默认行为",
                "method": "local",
                "is_builtin": True,
                "created_at": now,
            },
            {
                "name": "简洁要点",
                "description": "单段、低温、更确定",
                "method": "local",
                "response_type": "single paragraph",
                "temperature": 0.2,
                "is_builtin": True,
                "created_at": now,
            },
            {
                "name": "详尽调研",
                "description": "global、粗社区、多段",
                "method": "global",
                "community_level": 1,
                "response_type": "multiple paragraphs",
                "temperature": 0.3,
                "is_builtin": True,
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("query_preset")
