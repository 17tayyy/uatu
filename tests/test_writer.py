import asyncio
from datetime import UTC, datetime, timedelta

from uatu.recorder.messages import StaticRow
from uatu.recorder.writer import BatchWorker, merge_vessels

T0 = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)


class Recorder(BatchWorker[int]):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.batches: list[list[int]] = []

    async def _process(self, batch):
        if batch:
            self.batches.append(batch)


async def test_worker_flushes_full_batches_without_waiting():
    worker = Recorder(batch_size=3, flush_interval_s=60, queue_max=100)
    for i in range(7):
        worker.offer(i)
    worker.start()
    await asyncio.sleep(0.05)
    assert worker.batches == [[0, 1, 2], [3, 4, 5]]
    await worker.close(timeout_s=1)


async def test_worker_flushes_partial_batches_after_the_interval():
    worker = Recorder(batch_size=100, flush_interval_s=0.05, queue_max=100)
    worker.start()
    worker.offer(1)
    await asyncio.sleep(0.2)
    assert worker.batches == [[1]]
    await worker.close(timeout_s=1)


async def test_close_drains_the_queue_without_waiting_for_the_interval():
    worker = Recorder(batch_size=10, flush_interval_s=60, queue_max=100)
    worker.start()
    for i in range(25):
        assert worker.offer(i)
    await asyncio.sleep(0.01)
    async with asyncio.timeout(1):
        await worker.close(timeout_s=5)
    assert [i for batch in worker.batches for i in batch] == list(range(25))
    assert worker.task.done()


def test_worker_drops_when_the_buffer_is_full():
    worker = Recorder(batch_size=10, flush_interval_s=1, queue_max=2)
    assert worker.offer(1) and worker.offer(2)
    assert not worker.offer(3)


def test_merge_vessels_combines_partial_reports_per_mmsi():
    reports = [
        StaticRow(1, T0 + timedelta(minutes=1), 24, name="LUNA"),
        StaticRow(2, T0, 5, name="NORDIC TANKER", imo=9876543),
        StaticRow(1, T0, 24, callsign="EA1234", ship_type=36, dim_a=10, dim_b=2),
        StaticRow(1, T0 + timedelta(minutes=2), 24, name="LUNA II"),
    ]
    merged = {row["mmsi"]: row for row in merge_vessels(reports)}
    assert merged[1] | {} == merged[1] and set(merged) == {1, 2}
    luna = merged[1]
    assert (luna["name"], luna["callsign"], luna["ship_type"], luna["dim_a"]) == ("LUNA II", "EA1234", 36, 10)
    assert luna["ais_class"] == "B"
    assert luna["first_seen_at"] == T0
    assert luna["last_static_at"] == T0 + timedelta(minutes=2)
    assert merged[2]["ais_class"] == "A"
