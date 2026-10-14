# Uatu

Dark vessel detection in the Strait of Gibraltar: finding ships that appear on Sentinel-1
radar imagery but are not broadcasting AIS.

**Status:** running 24/7 on a homelab. The AIS recorder stores everything aisstream.io
sends for the area (it only streams live data: any day not recorded is lost); a worker
looks for suspicious behaviour in that AIS every 10 minutes, and picks up every new
Sentinel-1 pass to detect ships and pair them with it.

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

## AIS analysis

Every 10 minutes the worker runs detectors ([`src/uatu/ais/events.py`](src/uatu/ais/events.py))
over the last 6 hours of AIS and stores what they find in `ais_events`:

| Event | What | Notable when |
|---|---|---|
| `ais_gap` | a vessel stops transmitting for 1 h+ while vessels around it are still heard | under way, at sea, and vessels are also heard where it was heading (so it did not just sail out of the area, into port, or into a hole in aisstream's coverage) |
| `encounter` | two vessels within 500 m, both under 2 kn, for 2 h+ (ship-to-ship transfers) | > 5 km from the coast |
| `loitering` | nearly stopped for 3 h+ | > 5 km from the coast |
| `infrastructure_loitering` | under 3 kn for 30 min+ within 1 km of a submarine cable or pipeline | where an anchor can reach the seabed, ≤ 200 m deep (alert) |
| `sanctioned_vessel` | a vessel on a sanctions list, by IMO (or listed MMSI) | always (alert) |
| `identity_change`, `shared_imo` | new name / IMO / callsign / type; two MMSIs with the same valid IMO | name or IMO changes |
| `position_jump` | positions further apart than any ship could sail (spoofing, broken GPS) | informative |

Anything happening where 4+ vessels have sat stopped within 8 km over the last 7 days (an
anchorage or waiting area, learnt from the record itself) is routine and only informative. Alerts with severity 3 are
pushed to the phone at once; notable ones (2) go into a summary at 08:00 and 20:00 UTC.

Reference data, refreshed by the worker:

- **Sanctioned vessels**: [OpenSanctions](https://www.opensanctions.org) `sanctions`
  collection (EU, UK, US, CH, CA, AU lists), CC BY-NC 4.0, daily.
- **Water depth**: [EMODnet Bathymetry](https://emodnet.ec.europa.eu/en/bathymetry) DTM (~115 m,
  CC BY 4.0), downloaded once for the area: a vessel over a cable can only harm it by anchoring.
- **Infrastructure in Spanish waters**: [EMODnet Human Activities](https://emodnet.ec.europa.eu/en/human-activities)
  (telecom cables from Spain's CICA, pipelines, platforms, ports) and
  [TeleGeography](https://www.submarinecablemap.com)'s submarine cable map (CC BY-NC-SA), weekly.

```sh
uv run uatu-ais around 36.10 -5.40 --at 2026-10-13T18:25Z   # who was there: AIS, events, radar
uv run uatu-ais events --hours 24
```

## SAR ship detection

```sh
uv run uatu-sar search                 # Sentinel-1 passes over the area in the last week
uv run uatu-sar download --latest      # needs CDSE_USERNAME / CDSE_PASSWORD
uv run uatu-sar detect data/sar/<scene>.zip --ais
```

`detect` writes `detections.geojson`, `ais.geojson` and `quicklook.png` next to the scene.
Scenes are processed tile by tile (20 km tiles with a 1.6 km halo, so CFAR windows and the
coastal buffer see past tile edges): a frame takes ~15 s and ~0.75 GB of RAM.

1. **Calibration**: digital numbers to backscatter, sigma0 = DN² / A², with A interpolated
   from the product's calibration look-up table.
2. **Geocoding**: warped to a 10 m UTM grid from the product's tie points (thin plate
   spline). No terrain correction, which is exact at sea level.
3. **Land mask**: ESA WorldCover 2021 (10 m), read remotely from its Cloud Optimised
   GeoTIFFs, plus a 300 m coastal buffer: harbours and breakwaters look like ships.
4. **Detection**: two-parameter CFAR on VV (pixels more than k standard deviations above
   the surrounding sea), hits grouped into objects. Then dropped: faint objects (speckle in
   calm water near the noise floor), objects standing < 12 dB above the surrounding sea
   (rough sea at low incidence), objects dark in VH (ships return in cross-polarisation
   too) and the sidelobes of very bright targets.
5. **AIS at acquisition time**: each vessel's track is interpolated (or dead-reckoned from
   its speed and course) to the instant the radar looked at that spot, from the per-line
   azimuth times in the product annotation.
6. **Moving-target shift**: a ship's motion along the radar's line of sight shifts it along
   the track by -(R/V) * range rate, ~110 s x range rate for Sentinel-1, often hundreds of
   metres. Expected AIS positions get the same shift.
7. **Pairing**: one-to-one assignment (Hungarian method) minimising distances measured in
   units of each vessel's tolerance: 1 km, plus a share of how far the vessel travels
   between its nearest AIS report and the acquisition. Vessels just outside the searched
   sea can still pair; if they don't, they count as not imaged rather than missed.
8. **AIS coverage**: an unpaired detection is a **dark vessel candidate** only where
   aisstream.io was receiving AIS right there: at least 3 other vessels heard within 3 km
   and ±2 h, each heard 3+ times on average. Coverage has holes at the scale of a bay: in the
   strait's lanes vessels are heard 10-20 times in 4 h, in the anchorages behind the Rock of
   Gibraltar about once, so it is measured locally and by quality, not just presence.
   Elsewhere the detection is `no_coverage`: nothing can be said about it.
9. **Who is the dark vessel?** Unpaired detections are paired in turn, the same way, with
   vessels heard within 3 h of the acquisition but silent around it, placed across their
   silence (interpolated, or dead-reckoned from the last report) with a tolerance growing
   with how long they had been silent. Most turn out to be reception gaps (silent < 1 h:
   aisstream.io hears many vessels only every few minutes); a match after a longer silence
   names a vessel that may have switched its transponder off.
10. **Fixed objects**: detections of all passes are clustered (PostGIS `ST_ClusterDBSCAN`,
    150 m); a place detected in 3+ passes and in at least half of the passes covering it is a
    fixed object (platform, buoy, fish farm, long-term mooring), not a candidate. Historical
    passes, processed without AIS, teach this from the start (`uatu-sar backfill --days 90`).

Missing AIS does not mean a vessel is hiding: small craft often carry none, aisstream.io
coverage has gaps (see `ingest_gaps`), and it delivers about one position per vessel per
minute.

### Worker

`worker` ([`src/uatu/worker.py`](src/uatu/worker.py)) runs the same pipeline on
[ArdiQ](https://github.com/17tayyy/ardiq) with Redis:

- `sar.discover`, every 10 minutes: Sentinel-1 frames over the area acquired since AIS
  recording began (frames covering < 10% are skipped), recorded in `sar_scenes` and queued.
  The catalogue can omit fresh products for a few minutes; a later run finds them.
- `sar.process_scene`: download, detect, pair, link suspects, store, rebuild fixed objects;
  retried 3 times 10 minutes apart, then marked `failed`. One at a time (~0.8 GB of RAM).
  Products are deleted once processed. Each live pass ends with a notification: candidates
  where AIS is received, split into reception gaps, vessels that went silent (high priority
  if sanctioned) and unidentified ones.
- `sar.backfill`: on demand, queues historical passes on a low-priority lane.

Results land in `sar_detections` (one row per ship, with `status` matched / dark /
no_coverage and the time the radar saw it) and `sar_ais_checks` (each AIS vessel the scene
should have shown, detected or not), plus GeoJSON and a quicklook in the `sardata` volume.

```sh
uv run uatu-sar candidates --days 7    # dark vessel candidates of recent passes
```

```sql
-- Dark vessel candidates, latest first
SELECT s.acquired_from, d.lat, d.lon, d.length_m, d.suspected_mmsi, d.suspect_silent_minutes
FROM sar_detections d JOIN sar_scenes s ON s.id = d.scene_id
LEFT JOIN sar_fixed_objects f ON f.id = d.fixed_object_id
WHERE d.status = 'dark' AND f.fixed IS NOT TRUE
ORDER BY d.seen_at DESC;
```

## Dashboard

A map and a side panel at `http://<host>:8000` (the `web` service), in Spanish:

- **Pasadas**: every Sentinel-1 pass since AIS recording began, with the radar image laid on
  the map and each detection coloured by what AIS says about it (paired, where AIS is barely
  received, a fixed object, or a candidate with no AIS). Candidates come first, with the vessel
  that went silent and could be there.
- **Eventos**: the AIS events (gaps, encounters, loitering, vessels over cables, sanctioned
  vessels...) of the last 24 h to 7 days.
- **Barcos**: the vessels heard in the last hour (sanctioned ones first), search by name or
  MMSI, and a vessel's card: track, events, sanctions and the passes that saw it.
- Layers for live vessels, infrastructure and AIS reception quality, refreshed every minute.

It draws its own base map with MapLibre GL, no third-party tiles: land from the SAR land mask in
the watched area, and from [Natural Earth](https://www.naturalearthdata.com) (public domain) around
it, dimmed. The radar image covers the part of the area inside the satellite's frame, which is
tilted (Sentinel-1 flies a near-polar orbit and looks sideways), so a pass can leave a corner of the
area without image.
The API behind it is read-only, under `/api/v1` (documented at `/docs`).

**It has no authentication: keep it on the local network, never forward the port.**

Scenes processed before the dashboard existed have no radar overlay
(`backscatter.png` + `backscatter.json` in the scene's output folder); the worker writes it for
every new pass.

## Operations

The worker also keeps the system healthy:

- `ops.backup`, daily at 03:00 UTC: `pg_dump` into the `backups` volume, 14 kept. They live
  on the same disk as the database, so also back up the container (e.g. Proxmox `vzdump`).
- `ops.health`, every 5 minutes: AIS still arriving (< 15 min), disk > 10% free, last backup
  < 26 h old. Changes are pushed to a phone through [ntfy](https://ntfy.sh) (`NTFY_URL`), as
  are tasks that fail for good (periodic ones only after 3 failures in a row: one network
  blip fixes itself on the next run). A notification that cannot be delivered, often because
  the connection itself is down, is retried on the next check. The twice-daily AIS digest
  also lists the periods with no AIS recorded. With `HEALTHCHECK_URL` (e.g. healthchecks.io) it also pings a
  dead man's switch, which alerts when the worker or the whole machine goes quiet.

Restoring a backup, into an empty database (`--clean` cannot drop PostGIS while tables use it):

```sh
docker compose exec -T worker ls /data/backups
docker compose exec -T worker cat /data/backups/<dump> > uatu.dump
docker compose stop recorder worker
docker compose exec -T db sh -c 'dropdb -U uatu uatu && createdb -U uatu uatu'
docker compose exec -T db pg_restore -U uatu -d uatu --no-owner < uatu.dump
docker compose up -d
```

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
LOG_FORMAT=console uv run fastapi dev                  # the dashboard, on http://127.0.0.1:8000

uv run pytest                                          # unit tests
# Integration tests need a migrated database of their own (they assume nothing else in it):
docker compose exec db createdb -U uatu uatu_test
DATABASE_URL=${DATABASE_URL%/uatu}/uatu_test uv run alembic upgrade head
UATU_TEST_DATABASE_URL=${DATABASE_URL%/uatu}/uatu_test uv run pytest
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
| `AIS_BBOX`           | `35.5,-6.5,36.5,-4.5`                 | `lat_min,lon_min,lat_max,lon_max`; recorder and AIS analysis |
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
| `REDIS_URL`          | `redis://localhost:6379`              | ArdiQ broker, set by docker compose        |
| `SAR_DISCOVER_MINUTES` | `10`                                | how often the worker looks for new scenes  |
| `SAR_LOOKBACK_DAYS`  | `3`                                   | how far back it looks                      |
| `SAR_MIN_COVERAGE`   | `0.1`                                 | skip frames covering less of the area      |
| `SAR_KEEP_SCENES`    | `false`                               | keep products (~1-2 GB each) after processing |
| `NTFY_URL`           | unset                                 | `https://ntfy.sh/<unguessable topic>` for alerts |
| `HEALTHCHECK_URL`    | unset                                 | dead man's switch ping URL                 |
| `BACKUP_DIR` / `BACKUP_KEEP` | `data/backups` / `14`         | `/data/backups` volume in docker compose   |
| `AIS_STALE_MINUTES`  | `15`                                  | alert when no AIS arrives for this long    |
| `MIN_FREE_DISK`      | `0.1`                                 | alert below this fraction of free disk     |
| `WEB_PORT`           | `8000`                                | dashboard port on the host (docker compose) |
