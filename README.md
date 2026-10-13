# Uatu

Dark vessel detection in the Strait of Gibraltar: finding ships that appear on Sentinel-1
radar imagery but are not broadcasting AIS.

**Status:** the AIS recorder runs 24/7, so there is ground truth for every satellite pass
(aisstream.io only streams live data: any day not recorded is lost). Ship detection on
Sentinel-1 and pairing with AIS work by hand, scene by scene; automation comes next.

## AIS recorder

```
aisstream.io ──websocket──▶ parse ──▶ bounded queue ──batches──▶ PostGIS
                              │
                              └──────▶ bounded queue ──batches──▶ raw archive (.jsonl.gz)
```

- Subscribes to the area of interest (default lat 35.5–36.5, lon -6.5 – -4.5).
- Reconnects with exponential backoff and jitter, and also when an open connection goes
  silent (`STALE_TIMEOUT_S`).
- Buffers in memory and writes in batches. If Postgres goes down, the batch is retried
  until it comes back; messages keep accumulating up to `QUEUE_MAX`.
- Records every connection in `ingest_sessions`. The `ingest_gaps` view lists the periods
  when Uatu was blind, so that missing AIS there is not mistaken for a vessel going dark.
- Archives every raw message (all types, not only the parsed ones) to hourly gzipped JSON
  Lines files for reprocessing.
- Logs JSON lines, with a `stats` event every minute; drains its buffers on `SIGTERM`.

### Data model

| Table             | Contents                                                                     |
| ----------------- | ---------------------------------------------------------------------------- |
| `positions`       | One row per position report (AIS types 1, 2, 3, 18, 19, 27), with a PostGIS `geography` point |
| `static_reports`  | Every static/voyage report (types 5, 19, 24): history of names, callsigns, dimensions |
| `vessels`         | Latest known static data per MMSI, including `length_m`/`beam_m` for matching radar detections |
| `ingest_sessions` | Websocket connections to aisstream.io; see the `ingest_gaps` view               |

The schema is declared in [`src/uatu/db/models.py`](src/uatu/db/models.py) (SQLAlchemy +
GeoAlchemy2) and versioned with Alembic in [`migrations/`](migrations/).

## SAR ship detection

```sh
uv run uatu-sar search                 # Sentinel-1 passes over the area in the last week
uv run uatu-sar download --latest      # needs CDSE_USERNAME / CDSE_PASSWORD
uv run uatu-sar detect data/sar/<scene>.zip --ais
```

`detect` writes `detections.geojson`, `ais.geojson` and `quicklook.png` next to the scene.

1. **Calibration**: digital numbers to backscatter, sigma0 = DN² / A², with A interpolated
   from the product's calibration look-up table.
2. **Geocoding**: warped to a 10 m UTM grid from the product's tie points (thin plate
   spline). No terrain correction, which is exact at sea level.
3. **Land mask**: ESA WorldCover 2021 (10 m), read remotely from its Cloud Optimised
   GeoTIFFs, plus a 300 m coastal buffer: harbours and breakwaters look like ships.
4. **Detection**: two-parameter CFAR on VV (pixels more than k standard deviations above
   the surrounding sea), hits grouped into objects. Then faint objects (speckle in calm,
   dark water near the noise floor) and the sidelobes of very bright targets are dropped.
5. **AIS at acquisition time**: each vessel's track is interpolated (or dead-reckoned from
   its speed and course) to the instant the radar looked at that spot, from the per-line
   azimuth times in the product annotation.
6. **Moving-target shift**: a ship's motion along the radar's line of sight shifts it along
   the track by -(R/V) * range rate, ~110 s x range rate for Sentinel-1, often hundreds of
   metres. Expected AIS positions get the same shift.
7. **Pairing**: one-to-one assignment minimising total distance (Hungarian method), within
   1 km. Unpaired detections are **dark vessel candidates**. AIS vessels outside the
   searched sea are reported apart from those the radar missed.

Missing AIS does not mean a vessel is hiding: small craft often carry none, aisstream.io
coverage has gaps (see `ingest_gaps`), and it delivers about one position per vessel per
minute.

## Running

```sh
cp .env.example .env    # set AISSTREAM_API_KEY (free at aisstream.io) and POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f recorder
```

`migrate` applies pending migrations before `recorder` starts. Postgres is only exposed on
the host's `127.0.0.1`. To reach it from another machine (QGIS, DBeaver...), use an SSH tunnel:
`ssh -L 5432:localhost:5432 <host>`.

```sql
-- Vessels within 2 km of a point, 5 minutes either side of a satellite pass
SELECT DISTINCT p.mmsi, v.name, v.length_m
FROM positions p LEFT JOIN vessels v USING (mmsi)
WHERE ST_DWithin(p.geom, ST_Point(-5.6, 36.0, 4326)::geography, 2000)
  AND p.received_at BETWEEN '2026-10-13 06:25Z' AND '2026-10-13 06:35Z';
```

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync
docker compose up -d db
uv run alembic upgrade head
LOG_FORMAT=console uv run uatu-recorder

uv run pytest                                          # unit tests
UATU_TEST_DATABASE_URL=$DATABASE_URL uv run pytest     # plus integration tests against PostGIS
uv run ruff check . && uv run ruff format .
```

Schema changes: edit `models.py`, run `uv run alembic revision --autogenerate -m "..."`,
review the generated file in `migrations/versions/`, then run `uv run alembic upgrade head`.

## Configuration

Environment variables (or `.env`):

| Variable             | Default                               | Notes                                      |
| -------------------- | ------------------------------------- | ------------------------------------------ |
| `AISSTREAM_API_KEY`  | required                              |                                            |
| `DATABASE_URL`       | `postgresql+asyncpg://uatu:uatu@localhost:5432/uatu` | set by docker compose       |
| `AIS_BBOX`           | `35.5,-6.5,36.5,-4.5`                 | `lat_min,lon_min,lat_max,lon_max`          |
| `RAW_ARCHIVE_DIR`    | unset (disabled)                      | `/data/raw` volume in docker compose       |
| `BATCH_SIZE`         | `1000`                                | rows per database transaction              |
| `FLUSH_INTERVAL_S`   | `2`                                   | max wait before writing a partial batch    |
| `QUEUE_MAX`          | `200000`                              | buffered messages before dropping          |
| `STALE_TIMEOUT_S`    | `60`                                  | reconnect if no message arrives for this long |
| `BACKOFF_INITIAL_S` / `BACKOFF_MAX_S` | `1` / `60`           | reconnection backoff                       |
| `STATS_INTERVAL_S`   | `60`                                  |                                            |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json`                 | `LOG_FORMAT=console` for local development |
| `CDSE_USERNAME` / `CDSE_PASSWORD` | unset                    | [Copernicus Data Space](https://dataspace.copernicus.eu) account, to download scenes |
| `SAR_BBOX`           | `35.5,-6.5,36.5,-4.5`                 | area for SAR search and detection          |
| `SAR_DATA_DIR`       | `data/sar`                            | downloaded scenes and outputs              |
