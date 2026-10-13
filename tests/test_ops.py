import os
import time
from datetime import timedelta
from pathlib import Path

from uatu.ops import Alerter, Check, check_backup, check_disk, to_prune


def test_to_prune_keeps_the_newest_dumps():
    dumps = [Path(f"uatu-202610{day:02d}T0300Z.dump") for day in (13, 11, 12, 14)]
    assert to_prune(dumps, keep=2) == [Path("uatu-20261011T0300Z.dump"), Path("uatu-20261012T0300Z.dump")]
    assert to_prune(dumps, keep=10) == []


async def test_alerter_notifies_on_changes_only():
    sent: list[tuple[str, str]] = []

    async def send(title: str, message: str, priority: str, link: str | None) -> None:
        sent.append((title, priority))

    alerter = Alerter(send)
    ok, failing = Check("ais", True, "1 min ago"), Check("ais", False, "40 min ago")
    for checks in ([ok], [failing], [failing], [failing], [ok], [ok]):
        await alerter.update(checks)
    assert sent == [("Uatu: ais failing", "high"), ("Uatu: ais recovered", "default")]


def test_disk_and_backup_checks(tmp_path: Path):
    assert check_disk(tmp_path, min_free=0.0).ok
    assert not check_disk(tmp_path, min_free=1.0).ok

    assert check_backup(tmp_path / "missing", max_age=timedelta(hours=26)).ok  # nothing yet is not a failure
    dump = tmp_path / "uatu-20261013T0300Z.dump"
    dump.write_bytes(b"x")
    assert check_backup(tmp_path, max_age=timedelta(hours=26)).ok
    two_days_ago = time.time() - 48 * 3600
    os.utime(dump, (two_days_ago, two_days_ago))
    stale = check_backup(tmp_path, max_age=timedelta(hours=26))
    assert not stale.ok and "48 h" in stale.detail
