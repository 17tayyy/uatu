"""depth under wakes

Revision ID: 8f2b6d4e1a93
Revises: 5d0e9a7c3b21
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f2b6d4e1a93"
down_revision: str | Sequence[str] | None = "5d0e9a7c3b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sar_detections",
        sa.Column(
            "wake_depth_m",
            sa.REAL(),
            nullable=True,
            comment="of the water under it (median): in shallow water the seabed passes for wakes",
        ),
    )


def downgrade() -> None:
    op.drop_column("sar_detections", "wake_depth_m")
