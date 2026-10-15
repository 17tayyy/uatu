"""ais gaps when uatu was deaf

AIS gaps whose first hour overlaps one of Uatu's own outages of 5 minutes or more (its connection
or the recorder down) say nothing: lowered to informative and marked uatu_blind, as the detectors
now do. CHEM NICHOLAS's on 13 October was one: last heard at 21:55 UTC off Tarifa, Uatu's
connection dropped at 22:34.

Revision ID: 4279318cff39
Revises: 1c423a3c691f
Create Date: 2026-10-15

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4279318cff39"
down_revision: str | Sequence[str] | None = "1c423a3c691f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE ais_events e
        SET severity = 1, details = details || '{"uatu_blind": true}'::jsonb, updated_at = now()
        WHERE kind = 'ais_gap'
          AND severity > 1
          AND EXISTS (
              SELECT 1 FROM ingest_gaps g
              WHERE g.duration >= interval '5 minutes'
                AND g.gap_start < e.start_at + interval '1 hour'
                AND g.gap_end > e.start_at
          )
        """
    )


def downgrade() -> None:
    """Nothing to undo: the detectors would mark them the same way."""
