"""identity changes that flip or are corrections

The stored identity changes, judged as the detector now does: a change back to a value the vessel
had used, or one undone within the hour, is no change (two transponders on one MMSI, a corrupt
message): deleted. A name changed by a letter or two is a correction: informative.

Revision ID: d6e3b7623203
Revises: 1d2d32f88f13
Create Date: 2026-10-15

"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e3b7623203"
down_revision: str | Sequence[str] | None = "1d2d32f88f13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, mmsi, start_at, details FROM ais_events WHERE kind = 'identity_change'"
            " ORDER BY mmsi, details->>'field', start_at"
        )
    ).all()
    groups = defaultdict(list)
    for row in rows:
        groups[row.mmsi, row.details["field"]].append(row)
    drop, minor = [], []
    for (_, field), changes in groups.items():
        known = set()
        for i, c in enumerate(changes):
            before, after = c.details["before"], c.details["after"]
            known.add(before)
            undone = any(
                later.details["after"] == before and later.start_at - c.start_at <= timedelta(hours=1)
                for later in changes[i + 1 :]
            )
            if after in known or undone:
                drop.append(c.id)
            elif (
                field == "name"
                and min(len(str(before)), len(str(after))) >= 6
                and _distance(str(before), str(after)) <= 2
            ):
                minor.append(c.id)
            known.add(after)
    if drop:
        conn.execute(sa.text("DELETE FROM ais_events WHERE id = ANY(:ids)"), {"ids": drop})
    if minor:
        conn.execute(
            sa.text(
                "UPDATE ais_events SET severity = 1, details = details || '{\"correction\": true}'::jsonb,"
                " updated_at = now() WHERE id = ANY(:ids)"
            ),
            {"ids": minor},
        )


def downgrade() -> None:
    """Nothing to restore: the detector would not find them again."""
