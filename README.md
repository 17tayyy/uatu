# Uatu

[![CI](https://github.com/17tayyy/uatu/actions/workflows/ci.yml/badge.svg)](https://github.com/17tayyy/uatu/actions/workflows/ci.yml)

**Dark vessel detection on the Spanish Mediterranean coast**: ships that Sentinel-1's radar sees
but that are not broadcasting AIS, and the ones that are broadcasting but deserve a second look
(sanctioned tankers sailing under new names, transponders switched off at sea, ship-to-ship
meetings, vessels idling over submarine cables).

![The dashboard on the Strait of Gibraltar: a Sentinel-1 pass, each radar detection coloured by what AIS says about it](docs/dashboard.png)

Running 24/7 on a homelab since 13 October 2026: the Strait of Gibraltar first, then the whole
coast from Gibraltar to Cap de Creus and the Balearic Islands, in six zones brought into the
analysis one at a time.

## What it does

- **Records AIS** for the whole coast, around the clock: free live feeds keep no history, so
  every day not recorded is lost. About 1,300 vessels an hour across the six zones.
- **Watches AIS for behaviour**: vessels going silent at sea (only where reception is good
  enough for silence to mean something), ship-to-ship meetings, loitering, idling over cables
  in water shallow enough to drop an anchor on them, identity changes, impossible jumps, and
  vessels whose IMO number is on a sanctions list.
- **Knows who is behind a vessel**: its owners and managers, from sanctions lists, Ukraine's
  military intelligence (GUR) and port state control records, so a vessel off the lists but run
  by a sanctioned company, or by one whose fleet mostly is, is flagged too.
- **Reads every Sentinel-1 pass** over the coast: calibrates the radar image, detects ships,
  places every AIS vessel where the radar saw it at that instant, pairs the two, and names the
  ships left over, separating those where AIS reception is too poor to judge.
- **Learns the coast**: three months of past passes teach it what never moves (platforms,
  buoys, fish farms, long-term moorings), so they are not flagged.
- **Tells**: a map dashboard per zone, and phone notifications for each pass and each alert.

## What it has found so far

From public data only (AIS as received by aisstream.io, sanctions lists via OpenSanctions):

- **Three tankers whose IMO numbers are on sanctions lists, through the Strait in two days.**
  *SPARTAN* (Omani flag, 273 m) is IMO 9421972, listed as *SCF SAMOTLOR* of Sovcomflot by the
  EU, UK, Canada, Australia and Switzerland, declaring Vysotsk to Port Said. *DERZHAVA* (Russian
  flag, 274 m, for Murmansk) is IMO 9290385, listed as *ELBUS*. *FLEXI 2* is IMO 9246322,
  listed as *TEX* under US Iran sanctions. Same hulls, new names: the shadow fleet at work.
- **And who runs them.** *FLEXI 2*'s ISM manager, Draco Buren Shipping of Singapore, is under US
  sanctions itself, and 13 of the 14 vessels it is known to manage are too. Four more tankers and
  a bulk carrier heard off the coast are on no sanctions list but on GUR's: *FENOR* and *CHEM
  NICHOLAS* as part of the Russian shadow fleet, *SUEZ PROTOPIA* and *SIREN* for carrying Russian
  oil out of the Baltic, *PANOREA* for Russian coal from Taman to Turkey.
- **What a radar pass looks like**, 14 October, 06:26 UTC over the Strait: 132 ships detected;
  36 paired with their AIS; 12 on fixed objects; 82 where aisstream hears too little to say
  whether they were broadcasting; one candidate, a 338 m echo where a container ship silent for
  90 minutes could have been. No dark vessel confirmed yet. That is the honest result of the
  first passes, and why the coverage test exists.

## How it works

![Architecture: recorder and worker on a homelab, PostGIS in the middle, dashboard and phone notifications out](docs/architecture.png)

The techniques (CFAR detection, Doppler shift, pairing by the Hungarian method, local AIS
coverage, fixed objects), why each is needed and what real data taught about them are in
[docs/how-it-works.md](docs/how-it-works.md). The diagram is an
[Excalidraw](https://excalidraw.com) file: [docs/architecture.excalidraw](docs/architecture.excalidraw).

Python 3.14 throughout: asyncio, SQLAlchemy 2 + GeoAlchemy2 on PostGIS (Alembic migrations),
numpy, scipy and rasterio for the radar, [ArdiQ](https://github.com/17tayyy/ardiq) (Rust core)
on Redis for tasks, FastAPI and MapLibre GL for the dashboard, Docker Compose on a Proxmox LXC.
CI runs the linters, the migrations on an empty PostGIS and 100+ tests on every change.

## What it cannot tell

- **Missing AIS is not hiding.** Small craft often carry no AIS; aisstream.io is a volunteer
  network with holes the size of a bay (vessels in the Strait's lanes are heard 10–20 times in
  4 hours, those anchored behind the Rock of Gibraltar about once); and it passes on about one
  position per vessel per minute. A detection with no AIS is a candidate only where reception
  around it is measured to be good.
- **Sentinel-1 sees ships, not who they are**, roughly from 25–30 m long, once every day or two
  per zone, and only the part of the coast its frame covers.
- **A listed IMO is a lead, not proof**: lists lag reality, and IMO numbers can be misreported.

## Responsible use

The dashboard has no authentication and stays on the local network. Nothing here publishes
live positions: migrant boats are a humanitarian matter, and tracking smugglers in real time
can put people at risk. Findings are written up aggregated and after the fact.

## AIS recorder

```
aisstream.io ──websocket──▶ parse ──▶ bounded queue ──batches──▶ PostGIS
                              │
                              └──────▶ bounded queue ──batches──▶ raw archive (.jsonl.gz)
```

- Subscribes to every zone in `uatu/config.py` (the Strait: lat 35.5–36.7, lon -6.5 – -4.5).
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
| `sanctioned_owner` | a vessel off the lists whose owner or manager is on one | always (alert) |
| `watchlisted_vessel` | off the lists, but run by a company with a mostly sanctioned fleet (2+ vessels, half or more), on GUR's lists (shadow fleet, vessels working for Russia), banned from European ports (Paris MoU) or insured by a sanctioned club | always |
| `identity_change`, `shared_imo` | new name / IMO / callsign / type; two MMSIs with the same valid IMO | name or IMO changes |
| `position_jump` | positions further apart than any ship could sail (spoofing, broken GPS) | informative |

Anything happening where 4+ vessels have sat stopped within 8 km over the last 7 days (an
anchorage or waiting area, learnt from the record itself) is routine and only informative. Alerts with severity 3 are
pushed to the phone at once; notable ones (2) go into a summary at 08:00 and 20:00 UTC.

Reference data, refreshed by the worker:

- **Sanctions, and who owns and manages vessels**: [OpenSanctions](https://www.opensanctions.org)
  (CC BY-NC 4.0), daily: the `sanctions` collection (EU, UK, US, CH, CA, AU... lists, with the
  companies they tie vessels to); Ukraine's GUR "War & Sanctions" (the shadow fleet and other
  vessels working for Russia, with their owners and their commercial and ISM managers); the Paris
  MoU's ban list; Tokyo and Black Sea MoU detentions of the last 3 years (the ISM company then).
  OpenSanctions resolves a company across sources to one id, which is what joins them. Vessels
  are keyed by IMO number throughout, which stays with a hull through renames and new flags.
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
    passes, processed without AIS, teach this from the start (`uatu-sar backfill <zone> --days 90`).

Missing AIS does not mean a vessel is hiding: small craft often carry none, aisstream.io
coverage has gaps (see `ingest_gaps`), and it delivers about one position per vessel per
minute.

### Worker

`worker` ([`src/uatu/worker.py`](src/uatu/worker.py)) runs the same pipeline on
[ArdiQ](https://github.com/17tayyy/ardiq) with Redis:

- `sar.discover`, every 10 minutes: Sentinel-1 frames over each zone acquired since AIS
  recording began there (frames covering under 10% of the zone's sea are skipped), recorded in
  `sar_scenes` and queued.
  The catalogue can omit fresh products for a few minutes; a later run finds them.
- `sar.process_scene`: download, detect, pair, link suspects, store, rebuild fixed objects;
  retried 3 times 10 minutes apart, then marked `failed`. Two at a time (~0.8 GB of RAM each).
  Products are deleted once processed. Each live pass ends with a notification: candidates
  where AIS is received, split into reception gaps, vessels that went silent (high priority
  if sanctioned) and unidentified ones.
- `sar.backfill`: on demand, queues a zone's historical passes on a low-priority lane.

Everything runs zone by zone (`ANALYSIS_ZONES`): each zone has its own radar grid (in its UTM
zone), land mask, coast distance and bathymetry. A frame over two zones is analysed for each
(a scene per zone) but downloaded once. A zone's passes are paired with AIS only from the moment
the recorder first subscribed to it (`recorded_zones`).

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

A map and a side panel at `http://<host>:8000` (the `web` service), in Spanish, over the whole
coast or one zone at a time (the selector in the header):

- **Pasadas**: every Sentinel-1 pass since AIS recording began, with the radar image laid on
  the map and each detection coloured by what AIS says about it (paired, where AIS is barely
  received, a fixed object, or a candidate with no AIS). Candidates come first, with the vessel
  that went silent and could be there.
- **Eventos**: the AIS events (gaps, encounters, loitering, vessels over cables, sanctioned
  vessels...) of the last 24 h to 7 days.
- **Barcos**: the vessels heard in the last hour (sanctioned and watchlisted ones first), search
  by name or MMSI, and a vessel's card: track, events, sanctions, owners and managers, what the
  lists say about it and the passes that saw it. A company's card lists its known fleet, with the
  vessels Uatu has heard first.
- Layers for live vessels, infrastructure and AIS reception quality, refreshed every minute.

The radar image of each pass is kept at 20 m (the sensor's actual resolution) as a tiled
Web Mercator GeoTIFF with overviews (`radar.tif`, ~17 MB a frame), and served as map tiles: the
whole frame from afar, each ship's echo when zoomed in. It is despeckled (Lee filter) and
coloured from both polarisations: dark navy where the sea is calm, white where something returns
strongly in VV and VH alike, as ships do. It covers the sea only: land comes from optical imagery,
[MapTiler](https://www.maptiler.com)'s satellite tiles with a key (`MAPTILER_KEY`, sent to the
browser: restrict it to the dashboard's origin), else [Sentinel-2 cloudless](https://s2maps.eu) by
EOX (10 m, CC BY-NC-SA 4.0). A "recent radar" layer mosaics the passes of the last week, newest on
top, so the whole coast shows its latest radar image at once.

Each detection's popup can show **a Sentinel-2 photo of its place**: the closest scene in time,
within 3 days, with the spot mostly free of cloud (by the scene's own classification), as a 4 km
chip read straight from the Cloud Optimised GeoTIFFs of Element 84's
[Earth Search](https://element84.com/earth-search/) catalogue on AWS, without downloading the
scene. Sentinel-2 passes around 11:00 UTC, hours from the radar: anchored ships, platforms and
fish farms show where the radar saw them, moving ships do not. Chips are kept in the `webcache`
volume; "none found" is looked up again after 6 hours, as scenes keep arriving.
It draws its own base map with MapLibre GL, no third-party tiles: land from the SAR land mask in
the watched area (its edge dashed), and from [Natural Earth](https://www.naturalearthdata.com)
(public domain) around it. The radar image, land included, covers the part of the area inside the
satellite's frame, which is tilted (Sentinel-1 flies a near-polar orbit and looks sideways): a pass
can leave part of the area without image. It is saved in Web Mercator, the projection web maps draw
in, so that each ship's echo lands under its detection.
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
| `ZONES`              | all six                               | zones the recorder records: `estrecho,alboran,murcia,valencia,cataluna,baleares` (`uatu/config.py`) |
| `ANALYSIS_ZONES`     | all six                               | zones the worker and the dashboard analyse; new ones are calibrated one at a time |
| `RAW_ARCHIVE_DIR`    | unset (disabled)                      | `/data/raw` volume in docker compose       |
| `BATCH_SIZE`         | `1000`                                | rows per database transaction              |
| `FLUSH_INTERVAL_S`   | `2`                                   | max wait before writing a partial batch    |
| `QUEUE_MAX`          | `200000`                              | buffered messages before dropping          |
| `STALE_TIMEOUT_S`    | `60`                                  | reconnect if no message arrives for this long |
| `BACKOFF_INITIAL_S` / `BACKOFF_MAX_S` | `1` / `60`           | reconnection backoff                       |
| `STATS_INTERVAL_S`   | `60`                                  |                                            |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json`                 | `LOG_FORMAT=console` for local development |
| `CDSE_USERNAME` / `CDSE_PASSWORD` | unset                    | [Copernicus Data Space](https://dataspace.copernicus.eu) account, to download scenes |
| `SAR_BBOX`           | `35.5,-6.5,36.7,-4.5`                 | area for the manual `uatu-sar` commands (`lat_min,lon_min,lat_max,lon_max`) |
| `SAR_DATA_DIR`       | `data/sar`                            | downloaded scenes and outputs              |
| `REDIS_URL`          | `redis://localhost:6379`              | ArdiQ broker, set by docker compose        |
| `SAR_DISCOVER_MINUTES` | `10`                                | how often the worker looks for new scenes  |
| `SAR_LOOKBACK_DAYS`  | `3`                                   | how far back it looks                      |
| `SAR_MIN_COVERAGE`   | `0.1`                                 | skip frames covering less of the zone's sea |
| `SAR_KEEP_SCENES`    | `false`                               | keep products (~1-2 GB each) after processing |
| `NTFY_URL`           | unset                                 | `https://ntfy.sh/<unguessable topic>` for alerts |
| `HEALTHCHECK_URL`    | unset                                 | dead man's switch ping URL                 |
| `BACKUP_DIR` / `BACKUP_KEEP` | `data/backups` / `14`         | `/data/backups` volume in docker compose   |
| `AIS_STALE_MINUTES`  | `15`                                  | alert when no AIS arrives for this long    |
| `MIN_FREE_DISK`      | `0.1`                                 | alert below this fraction of free disk     |
| `WEB_PORT`           | `8000`                                | dashboard port on the host (docker compose) |
| `MAPTILER_KEY`       | unset                                 | satellite imagery on land; else Sentinel-2 cloudless |
| `WEB_CACHE_DIR`      | `data/web-cache`                      | optical chips the dashboard fetched (`webcache` volume) |
