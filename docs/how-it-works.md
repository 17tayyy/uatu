# How Uatu works, and why

Each step below says what it does, why it is needed, and what real data from the Strait of
Gibraltar taught about it. Numbers are from the first passes and days of AIS (October 2026).

## 1. Recording AIS

Ships broadcast their position, speed, course and identity over AIS, a VHF radio system.
Volunteer receivers pick it up and [aisstream.io](https://aisstream.io) streams what they hear,
live only: it keeps no history. So the recorder runs around the clock, subscribed to six zones
of the Spanish Mediterranean coast, and writes every report to PostGIS in batches. It reconnects
on its own and logs every connection, so the gaps when Uatu itself was deaf are known and never
mistaken for a ship going quiet.

aisstream passes on about one report per vessel per minute: tracks are sparse, and everything
downstream interpolates them.

## 2. A radar that sees in the dark and through cloud

[Sentinel-1](https://sentiwiki.copernicus.eu/web/s1-mission) carries a C-band synthetic aperture
radar. It sends microwave pulses sideways and records the echoes. Calm sea reflects them away
from the satellite and looks dark. A ship, a steel box of corners, bounces them straight back and
looks like a bright point. The Interferometric Wide swath mode covers 250 km at 10 m pixels, day
or night, through cloud. With two satellites in orbit (1C and 1D) each zone is imaged every day
or two.

Uatu takes the GRD product: the image as intensities on the ground. A frame is 1–2 GB, about
250 × 170 km.

- **Calibration.** Raw pixel values become backscatter (σ⁰), the share of energy the surface
  returns: σ⁰ = DN² / A², with A interpolated from the product's own look-up table. Only then are
  pixels comparable across the image and across passes.
- **Geocoding.** The image is warped onto a 10 m grid in the zone's UTM projection, from tie
  points in the product. Terrain correction is skipped: at sea level it changes nothing.
- **Land.** ESA WorldCover (10 m) masks land, plus a 300 m strip along the coast: harbour walls
  and breakwaters are bright, angular and still, exactly like ships.
- **Tiles.** The image is processed in 20 km tiles, each with a 1.6 km margin so that windows near
  an edge still see sea around them. A frame takes about 15 seconds and 0.75 GB of RAM, within a
  2 GB homelab container.

## 3. Finding ships: CFAR

A fixed brightness threshold fails: the sea is darker in calm water, brighter in wind, and
brighter close to the satellite's track. **CFAR** (constant false alarm rate) judges each pixel
against its own surroundings instead. It estimates the mean and spread of the sea in a ring
between 500 m (a guard larger than any ship, so the ship itself does not inflate the statistics)
and 1,500 m around the pixel. It flags the pixel when it stands more than 6 standard deviations
above that mean. Flagged pixels within 30 m of each other make one object.

CFAR flags more than ships, and real data showed what else:

- **Rough sea** at low incidence angles is spiky: its peaks pass CFAR. They stand only 6–8 dB
  above the sea around them, while ships stand 15–40 dB above it, so objects under 12 dB of
  contrast are dropped.
- **Speckle** in very calm, dark water near the instrument's noise floor: objects under −5 dB
  are dropped.
- **Polarisation.** Ships return strongly in cross-polarisation too (VH between −20 and −7 dB for
  the first ships confirmed by AIS). Sea features flagged in VV sit near VH's noise floor,
  between −25 and −28 dB. Objects under −23 dB in VH are dropped. The threshold is provisional,
  set on 12 confirmed ships, and is revisited as more passes are paired.
- **Sidelobes.** A very bright ship smears into a cross of fainter echoes. Objects 15 dB fainter
  than a neighbour within 2 km are dropped.

## 4. Where was each AIS ship when the radar looked?

A radar frame is built line by line over 25 seconds, and a ship sails while it is imaged. Each
AIS track is interpolated to the exact instant the radar looked at that spot, read from the
per-line times in the product's annotation. Between two sparse reports the ship may have slowed
or turned: the tolerance grows with the distance it could have sailed since its nearest report.
That distance is computed from the faster of the two reports' speeds. A fast ferry slowing into
Ceuta (29 knots, then 7) went unpaired until it was.

**The Doppler shift.** A synthetic aperture radar places a target along the track from the
Doppler frequency of its echo. A ship moving towards or away from the satellite adds its own
Doppler, so the radar draws it displaced along the track by −(R/V) × range rate. With R ≈ 850 km
(slant range) and V ≈ 7.6 km/s (satellite speed), that is about 110 s × range rate: a ship
closing at 5 m/s appears about 560 m away from where it really is. Uatu shifts each expected AIS
position the same way. Validated on real pairs, the median distance between AIS and radar
dropped from about 530 m uncorrected to about 50 m.

## 5. Pairing: the Hungarian method

Several ships close together each have several possible echoes. Pairing each with its nearest
can give two ships the same echo. Uatu solves the assignment as a whole instead, one-to-one,
minimising the sum of distances. Each distance is measured in units of that ship's tolerance
(1 km plus a quarter of the distance it could have sailed since its nearest report), and pairs
beyond tolerance are forbidden. That is the classic assignment problem, solved exactly by the
Hungarian method (`scipy.optimize.linear_sum_assignment`).

## 6. No AIS: dark, or deaf?

A detection left without AIS is not yet a dark vessel. Uatu may simply not hear AIS there. The
Strait showed how sharp that is. Vessels in the shipping lanes are heard 10–20 times in 4 hours;
those anchored in the Bay of Algeciras, behind the Rock of Gibraltar, about once. A first,
coarse test, "were any vessels heard within 15 km?", produced dozens of false dark vessels in
the bay.

So coverage is judged **locally and by quality**. A detection counts as dark only if at least 3
other vessels were heard within 3 km and ±2 hours of the pass, each heard 3 times or more on
average. Anything else is "no AIS, poor reception". On the first full pass that was 82 of 132
detections: the honest measure of what free AIS can and cannot back up.

## 7. Who could it be?

For the detections still dark, Uatu runs the same assignment again, this time against vessels
heard within 3 hours of the pass but silent around it. Each is placed where it could be after
its silence, with a tolerance that grows with the length of that silence (up to 8 km). Silent
under an hour usually means a reception gap. Silent for longer names a vessel that may have
switched its transponder off, and the notification says so, flagged as high priority if its IMO
is on a sanctions list.

## 8. What never moves

Platforms, buoys, fish farms and long-term moorings come back pass after pass and are neither
ships to pair nor dark vessels. All detections are clustered in PostGIS (`ST_ClusterDBSCAN`,
150 m). A place seen in 3 or more passes, and in at least half of the passes that imaged it, is a
fixed object. Three months of past passes, processed without AIS on a low-priority queue, teach
each zone this before its first live pass.

## 9. Behaviour in the AIS itself

Every 10 minutes, detectors run over the last 6 hours of AIS:

- **AIS gaps**: a vessel under way, out at sea, that stops transmitting for over an hour while
  others around it are still heard. Others must also be heard where it was heading, or it may
  just have sailed out of coverage.
- **Encounters**: two vessels within 500 m, both under 2 knots, for over 2 hours, away from the
  coast. That is the signature of a ship-to-ship transfer.
- **Loitering**: nearly stopped for over 3 hours, away from the coast.
- **Vessels over infrastructure**: under 3 knots for over 30 minutes within 1 km of a submarine
  cable or pipeline. It is an alert only where the water is at most 200 m deep (EMODnet
  bathymetry), because a ship can only harm a cable by dragging an anchor. A tanker over a cable
  at 865 m deep had been a false alarm.
- **Sanctions**: IMO numbers (and listed MMSIs) checked against OpenSanctions' collection of EU,
  UK, US, Swiss, Canadian and Australian lists. Ships change names and flags; their IMO number
  stays.
- **Who is behind a vessel**: lists name a few thousand vessels, and the companies behind them
  run more. Sanctions lists tie vessels to their owners; Ukraine's military intelligence (GUR)
  names the owners and the commercial and ISM managers of the shadow fleet, sanctioned or not;
  the Paris MoU's ban list and port state detentions name the ISM company. OpenSanctions
  resolves one company across all of them to one id, so they join. A vessel off the lists is an
  alert when a company that owns or manages it is sanctioned. It is notable when the company
  runs at least 2 sanctioned vessels and they are at least half its known fleet, when it is on
  GUR's lists or banned from European ports. A share, not a count: one sanctioned hull in a
  large fleet says little about the rest, most of a fleet sanctioned says a lot. Detentions
  older than 3 years are left out, as ISM companies change.
- **Identity changes, shared IMO numbers, impossible jumps** (spoofing or broken GPS).

Where 4 or more vessels have sat stopped within 8 km over the past week, an anchorage or waiting
area learnt from the record itself, stopping is routine and only informative.

## 10. Lessons from running it

- **Measure what absence means before reporting it.** The coverage test (section 6) came from
  the first night of real data, not from a design document.
- **Projections bite.** The radar image is in UTM, and web maps stretch an image linearly in Web
  Mercator. Laid on the map as it was, it landed a median 330 m (up to 670 m) off across the
  Strait, every ship beside its own detection. It is now reprojected before saving: 40 m, under
  one pixel, checked by a test.
- **A zone's box is not its sea.** In Catalonia more than half the box is land. Frames were first
  chosen by how much of the box they covered, and 45 of 135 historical frames covered under 10%
  of the sea: 1–2 GB downloads for no ship. They are now chosen by sea covered.
- **Only something outside can say everything is down.** When the home router lost its
  connection one night, every component was fine, and silent. An external dead man's switch is
  the only alarm that works then.
