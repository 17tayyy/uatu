"""gaps where vessels usually go quiet

AIS gaps that began within 5 km of where at least 5 other vessels' gaps began in the 7 days before
are informative and marked usual_silence (how many), as the detectors now do: where aisstream
stops hearing ships, west of Tarifa above all, a silence says nothing about the vessel.

Revision ID: 1d2d32f88f13
Revises: 4279318cff39
Create Date: 2026-10-15

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d2d32f88f13"
down_revision: str | Sequence[str] | None = "4279318cff39"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH usual AS (
            SELECT e.id, (
                SELECT count(DISTINCT o.mmsi) FROM ais_events o
                WHERE o.kind = 'ais_gap' AND o.mmsi <> e.mmsi AND NOT o.details ? 'uatu_blind'
                  AND o.start_at BETWEEN e.start_at - interval '7 days' AND e.start_at
                  AND ST_DWithin(o.geom, e.geom, 5000)
            ) AS others
            FROM ais_events e
            WHERE e.kind = 'ais_gap' AND e.severity > 1
        )
        UPDATE ais_events e
        SET severity = 1, details = e.details || jsonb_build_object('usual_silence', u.others), updated_at = now()
        FROM usual u
        WHERE e.id = u.id AND u.others >= 5
        """
    )


def downgrade() -> None:
    """Nothing to undo: the detectors would mark them the same way."""
