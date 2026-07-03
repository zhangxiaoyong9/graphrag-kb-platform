# Copyright (c) 2024Microsoft Corporation.
# Licensed under the MIT License.
"""Add provider_profile.username + knowledge_base.neo4j_profile_id.

Read side of the Neo4j graph-query feature. ``username`` holds the Neo4j DB
user (neo4j kind only; NULL for llm/embedding profiles). ``neo4j_profile_id``
optionally links a KB to a neo4j provider-profile; when set, the cypher/hybrid
query methods read the KB's graph from Neo4j.

Both columns are added as plain nullable columns with no explicit FK
constraint, matching the migration 0005 precedent (SQLite cannot add FK
constraints to an existing table; the model-side ``ForeignKey`` declaration
is sufficient for ORM awareness, and SQLite does not enforce FK constraints
by default).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "provider_profile",
        sa.Column("username", sa.String(), nullable=True),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("neo4j_profile_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_base", "neo4j_profile_id")
    op.drop_column("provider_profile", "username")
