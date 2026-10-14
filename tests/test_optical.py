"""Optical chips: choosing a scene, making the chip, and the dashboard's endpoint (no network)."""

import io
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from PIL import Image

from uatu import optical

T0 = datetime(2026, 10, 14, 6, 26, tzinfo=UTC)


def item(name: str, hours_after: float) -> dict:
    at = (T0 + timedelta(hours=hours_after)).isoformat().replace("+00:00", "Z")
    return {"id": name, "properties": {"datetime": at, "platform": "sentinel-2a"}, "assets": {}}


async def test_the_closest_clear_scene_is_chosen(monkeypatch):
    scenes = [item("cloudy", 5), item("edge", -19), item("clear", 53), item("later", 72)]
    corners = [[-5.07, 36.22], [-5.01, 36.22], [-5.01, 36.18], [-5.07, 36.18]]
    reads = {
        "cloudy": (np.full((3, 4, 4), 90, np.uint8), 0.8, 1.0, corners),  # under cloud
        "edge": (np.zeros((3, 4, 4), np.uint8), 0.0, 0.4, corners),  # the point is at the edge of this tile
        "clear": (np.full((3, 4, 4), 30, np.uint8), 0.05, 1.0, corners),
    }

    async def around(http, lon, lat, at, days):
        return scenes

    monkeypatch.setattr(optical, "scenes_around", around)
    monkeypatch.setattr(optical, "read_chip", lambda it, lon, lat, half_m: reads[it["id"]])
    chip = await optical.best_chip(None, -5.04, 36.2, T0)
    assert (chip.scene, chip.cloud, chip.size_m, chip.corners) == ("clear", 0.05, 5000, corners)
    assert chip.acquired_at == T0 + timedelta(hours=53)
    assert Image.open(io.BytesIO(chip.jpeg)).size == (4, 4)

    reads["clear"] = (reads["clear"][0], 0.9, 1.0, corners)
    monkeypatch.setattr(optical, "read_chip", lambda it, lon, lat, half_m: reads.get(it["id"], reads["cloudy"]))
    assert await optical.best_chip(None, -5.04, 36.2, T0) is None


def test_the_stretch_lifts_a_dark_sea():
    rng = np.random.default_rng(0)
    sea = rng.integers(5, 25, (3, 100, 100)).astype(np.uint8)  # true colour open sea: nearly black
    sea[:, 49:51, 45:55] = 200  # a ship, 100 m long
    stretched = optical.stretch(sea)
    assert stretched[:, 50, 50].min() == 255  # the ship stands out...
    assert 3 * np.median(sea) < np.median(stretched) < 100  # ...the sea is lifted, but stays dark
    image = np.asarray(Image.open(io.BytesIO(optical.chip_jpeg(stretched))))
    assert image.shape == (100, 100, 3) and image[50, 50].min() > 200


@pytest.mark.skipif("UATU_TEST_DATABASE_URL" not in __import__("os").environ, reason="needs UATU_TEST_DATABASE_URL")
async def test_the_dashboard_looks_up_a_chip_once_and_keeps_it(tmp_path, monkeypatch):
    import os
    import uuid

    import httpx
    from sqlalchemy import delete, insert

    from uatu import db
    from uatu.db.models import SarDetection, SarScene
    from uatu.web.config import WebSettings
    from uatu.web.main import create_app
    from uatu.web.optical import service

    scene_id = uuid.uuid4()
    engine = db.create_engine(os.environ["UATU_TEST_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(
            insert(SarScene).values(
                id=scene_id,
                product_id=scene_id,
                zone="estrecho",
                name=f"S1C_OPTICAL_{scene_id.hex[:8]}",
                platform="S1C",
                datatake="0PT1C4",
                orbit_direction="DESCENDING",
                relative_orbit=1,
                acquired_from=T0,
                acquired_to=T0,
                footprint="SRID=4326;POINT(-5 36)",
                aoi_coverage=0.5,
                status="processed",
            )
        )
        detection_id = await conn.scalar(
            insert(SarDetection)
            .values(
                scene_id=scene_id,
                seen_at=T0,
                lat=36.2,
                lon=-5.04,
                status="dark",
                pixels=20,
                length_m=300.0,
                peak_db=5.0,
                vv_db=0.0,
            )
            .returning(SarDetection.id)
        )
    calls = []

    async def best_chip(http, lon, lat, at):
        calls.append((lon, lat, at))
        corners = [[-5.07, 36.22], [-5.01, 36.22], [-5.01, 36.18], [-5.07, 36.18]]
        return optical.Chip("S2A_30SUF", "sentinel-2a", at + timedelta(hours=5), 0.02, b"\xff\xd8\xff", 5000, corners)

    monkeypatch.setattr(service, "best_chip", best_chip)
    settings = WebSettings(database_url=os.environ["UATU_TEST_DATABASE_URL"], sar_data_dir=tmp_path,
                           web_cache_dir=tmp_path / "cache")  # fmt: skip
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for _ in range(2):
                    response = await client.get(f"/api/v1/detections/{detection_id}/optical")
                    assert response.status_code == 200
                    image = response.json()["data"]
                    assert (image["scene"], image["minutes_from_radar"], image["size_m"]) == ("S2A_30SUF", 300, 5000)
                    assert image["corners"][0] == [-5.07, 36.22]
                assert len(calls) == 1  # kept after the first look-up
                jpeg = await client.get(image["url"])
                assert jpeg.status_code == 200 and jpeg.headers["content-type"] == "image/jpeg"
                assert (await client.get("/api/v1/detections/999999999/optical")).status_code == 404
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(SarScene).where(SarScene.id == scene_id))
        await engine.dispose()
