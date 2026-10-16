"""wakes of detections

Revision ID: 5d0e9a7c3b21
Revises: b4e8d1c2a7f5
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d0e9a7c3b21"
down_revision: str | Sequence[str] | None = "b4e8d1c2a7f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    ("wake_margin", sa.REAL(), "from 2.5 on, wakes were right in validation"),
    ("wake_course_deg", sa.REAL(), None),
    ("wake_speed_kn", sa.REAL(), "through the water; null when the course runs along the satellite's track"),
    ("wake_lat", sa.Double(), "where the wake starts: the vessel, not its echo"),
    ("wake_lon", sa.Double(), None),
    ("wake_length_m", sa.REAL(), "of the stretch of wake that stood out"),
    ("wake_contrast", sa.REAL(), "to the sea beside it: -0.3 is 30 % darker"),
]


def upgrade() -> None:
    for name, type_, comment in COLUMNS:
        op.add_column("sar_detections", sa.Column(name, type_, nullable=True, comment=comment))


def downgrade() -> None:
    for name, _, _ in reversed(COLUMNS):
        op.drop_column("sar_detections", name)
