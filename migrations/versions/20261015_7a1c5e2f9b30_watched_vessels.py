"""watched vessels

Revision ID: 7a1c5e2f9b30
Revises: d6e3b7623203
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a1c5e2f9b30"
down_revision: str | Sequence[str] | None = "d6e3b7623203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watched_vessels",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("imo", sa.Integer(), nullable=True),
        sa.Column("mmsi", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=True, comment="as broadcast, in any case"),
        sa.Column("note", sa.String(), nullable=False, comment="why it is watched"),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "imo IS NOT NULL OR mmsi IS NOT NULL OR name IS NOT NULL", name=op.f("ck_watched_vessels_identified")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watched_vessels")),
    )


def downgrade() -> None:
    op.drop_table("watched_vessels")
