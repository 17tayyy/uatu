"""The report: its text from a made-up period, and the whole of it against a real database."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from uatu.config import ZONES
from uatu.report.data import Candidate, Heard, Listed, Notable, Report
from uatu.report.render import number, render

T0 = datetime(2026, 10, 14, 6, 26, tzinfo=UTC)


def made_up() -> Report:
    tanker = Listed(
        mmsi=636013409, name="CHEM NICHOLAS", imo=9374416, ship_type="Petrolero / tanque", length_m=183,
        status="watchlisted", why=["la inteligencia ucraniana (GUR) lo cuenta en la flota en la sombra rusa"],
        first_heard=T0, last_heard=T0 + timedelta(hours=6), transits=[(T0 + timedelta(hours=5), "west")],
        stops=[{"kind": "anchorage", "start": T0, "end": T0 + timedelta(hours=2, minutes=56), "place": "Gibraltar",
                "country": "Gibraltar", "place_km": 5.0, "lon": -5.3, "lat": 36.15}],
        track=[(-5.3, 36.15), (-5.6, 35.97)],
    )  # fmt: skip
    gap = Notable("ais_gap", 2, T0 + timedelta(hours=1), None, 248001000, "MISJE VIOLA", 35.97, -5.55,
                  {"silent_minutes": 90, "coast_km": 5.2, "ais_heard_nearby": 51})  # fmt: skip
    return Report(
        start=T0 - timedelta(days=7), end=T0, zones=[ZONES["estrecho"]], recorded=[ZONES["estrecho"]],
        heard=[Heard("Estrecho de Gibraltar", 123_456, 1_234)], vessels_heard=1_234,
        outages=[(T0 - timedelta(days=1), T0 - timedelta(days=1) + timedelta(hours=2, minutes=45))],
        passes=[], listed=[tanker], notable=[gap],
        candidates=[Candidate("S1C-013B0F-estrecho", T0, "unidentified", 40.0, -5.3, 36.1, None, None)],
        transits_by_day=[("2026-10-13", 79, 50)], stops_by_place=[], density=[],
    )  # fmt: skip


def test_numbers_and_the_text_in_spanish():
    assert (number(1234567), number(69.25, 1)) == ("1.234.567", "69,2")
    text = render(made_up(), {"listed": "barcos-vigilados.png"})
    assert text.startswith("# Uatu · del 7 oct al 14 oct de 2026")
    assert "**1.234 barcos** oídos por AIS" in text and "1 corte de Uatu" in text
    assert "### CHEM NICHOLAS · IMO 9374416" in text
    assert "Fondeado frente a Gibraltar, 2 h 56 min (14 oct 08:26-11:22)." in text  # not "Gibraltar (Gibraltar)"
    assert "Cruzó el Estrecho: 14 oct 13:26 hacia el Atlántico." in text
    assert "AIS apagado: MISJE VIOLA" in text  # by name, not by MMSI
    assert "![Derrotas y paradas de los barcos sancionados y vigilados](barcos-vigilados.png)" in text
    assert "## Límites" in text


@pytest.mark.skipif("UATU_TEST_DATABASE_URL" not in os.environ, reason="needs UATU_TEST_DATABASE_URL")
async def test_a_whole_report_with_its_figures(tmp_path):
    from uatu import db
    from uatu.report import figures
    from uatu.report.data import gather
    from uatu.report.write import write_report

    engine = db.create_engine(os.environ["UATU_TEST_DATABASE_URL"])
    try:
        end = datetime.now(UTC)
        report = await gather(engine, end - timedelta(days=3), end, [ZONES["estrecho"]], ZONES, tmp_path)
        # As the weekly task writes it: the folder named after the last whole day.
        folder, _ = await write_report(engine, end.date(), 7, [ZONES["estrecho"]], ZONES, tmp_path, tmp_path / "out")
    finally:
        await engine.dispose()
    assert folder.name == f"informe-{end.date() - timedelta(days=1)}"
    assert (folder / "informe.md").exists() and (folder / "informe.html").exists()
    report.listed = report.listed or made_up().listed  # the figures with something to draw, whatever the database
    report.transits_by_day = report.transits_by_day or made_up().transits_by_day
    report.density = report.density or [(-5.5, 36.0, 12), (-5.48, 36.0, 3)]
    figures.listed_map(report, tmp_path / "listed.png")
    figures.transits_chart(report, tmp_path / "transits.png")
    figures.density_map(report, tmp_path / "density.png")
    assert all((tmp_path / f).stat().st_size > 5_000 for f in ("listed.png", "transits.png", "density.png"))
    assert "## 5. Lo que Uatu oyó" in render(report, {})

    from uatu.report.html import to_html

    md = tmp_path / "informe.md"
    md.write_text(render(report, {"density": "density.png"}))
    page = to_html(md).read_text()
    assert page.startswith("<!doctype html>") and "data:image/png;base64," in page and "<table>" in page


def test_the_weekly_report_is_announced_with_a_link():
    from pathlib import Path

    from uatu.report.write import announcement

    title, message, link = announcement(
        Path("/data/reports/informe-2026-10-13"), made_up(), "http://192.168.1.41:8000/"
    )
    assert title == "Uatu: informe del 7 oct al 14 oct"
    assert message == "1.234 barcos oídos, 0 pasadas de radar, 1 candidato sin AIS, 1 barco sancionado o vigilado."
    assert link == "http://192.168.1.41:8000/api/v1/reports/informe-2026-10-13/informe.html"
    assert announcement(Path("informe-2026-10-13"), made_up(), None)[2] is None
