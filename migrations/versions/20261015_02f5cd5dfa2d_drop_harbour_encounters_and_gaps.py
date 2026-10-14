"""drop harbour encounters and gaps

Encounters and AIS gaps within 2 km of the coast are harbour life (vessels moored side by side,
going quiet at their berth): the detectors no longer keep them, and these are the ones kept
before. About 2,000 a day, nearly all in Barcelona's marinas.

Revision ID: 02f5cd5dfa2d
Revises: 21622a0183de
Create Date: 2026-10-15 00:29:26.356842

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "02f5cd5dfa2d"
down_revision: str | Sequence[str] | None = "21622a0183de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM ais_events
        WHERE kind IN ('encounter', 'ais_gap')
          AND jsonb_typeof(details -> 'coast_km') = 'number'
          AND (details ->> 'coast_km')::float < 2
        """
    )


def downgrade() -> None:
    """Nothing to restore: the detectors would not find them again either."""
