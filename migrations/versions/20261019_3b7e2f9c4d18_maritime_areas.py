"""maritime areas

Revision ID: 3b7e2f9c4d18
Revises: 8f2b6d4e1a93
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3b7e2f9c4d18"
down_revision: str | Sequence[str] | None = "8f2b6d4e1a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maritime_areas",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False, comment="Marine Regions MRGID"),
        sa.Column("name", sa.String(), nullable=False, comment="in Spanish, for people"),
        sa.Column("kind", sa.String(), nullable=False, comment="territorial_sea | disputed"),
        sa.Column(
            "claimants",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            comment="states claiming it, Marine Regions' names",
        ),
        sa.Column("source_name", sa.String(), nullable=False, comment="as Marine Regions names it"),
        sa.Column(
            "geom",
            Geography(
                geometry_type="MULTIPOLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maritime_areas")),
    )
    op.create_index("ix_maritime_areas_geom", "maritime_areas", ["geom"], unique=False, postgresql_using="gist")


def downgrade() -> None:
    op.drop_index("ix_maritime_areas_geom", table_name="maritime_areas", postgresql_using="gist")
    op.drop_table("maritime_areas")
