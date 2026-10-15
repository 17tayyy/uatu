// A vessel: who it is, how it stands with sanctions, who runs it, where it stopped, what it did.
import { ExternalLink } from "lucide-react";
import { useEffect } from "react";
import { useVessel } from "../../api/queries";
import type { CompanyRole, StopOut, TransitOut, VesselDetail as Vessel } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, COUNTRY, ROLE, SEVERITY, SOURCE, STOP, WATCHLIST } from "../../lib/constants";
import { ago, capitalise, duration, fmtTime, metres, month, sameDay, when } from "../../lib/format";
import { boundsOf, type LngLat } from "../../lib/geo";
import { fitBounds, flyTo, setData } from "../../map/instance";
import { ErrorNote, Glyph, Group, Header, Loading, mono, Note, Properties } from "../../ui";
import p from "../panel.module.css";
import { WatchBox } from "../watch/WatchBox";
import { ListingLabel } from "./VesselList";

function stopTitle(s: StopOut): string {
  const abroad = s.place_country && !["Spain", s.place].includes(s.place_country); // not "Gibraltar (Gibraltar)"
  const where = abroad ? `${s.place} (${COUNTRY[s.place_country!] ?? s.place_country})` : s.place;
  if (!where) return STOP[s.kind]?.title ?? s.kind;
  if (s.kind === "berth") return `Puerto de ${where}`;
  if (s.kind === "anchorage") return `Fondeado frente a ${where}`;
  return `Parado a ${Math.round(s.place_km ?? 0)} km de ${where}`;
}

function Voyage({ v }: { v: Vessel }) {
  const items = [
    ...v.stops.map((stop) => ({ at: stop.start_at, stop, transit: null as TransitOut | null })),
    ...v.transits.map((transit) => ({ at: transit.at, stop: null as StopOut | null, transit })),
  ].sort((a, b) => b.at.localeCompare(a.at));
  return (
    <ul className={p.list}>
      {items.map(({ stop, transit }) => {
        if (transit) {
          return (
            <li key={`t${transit.at}`}>
              <div className={p.row}>
                <Glyph kind="arrow" color={C.muted} size={13} />
                <span className={p.main}>
                  <span className={p.title}>Cruza el Estrecho</span>
                  <span className={p.meta}>
                    hacia {transit.direction === "east" ? "el Mediterráneo" : "el Atlántico"}
                  </span>
                </span>
                <span className={p.time}>{when(transit.at)}</span>
              </div>
            </li>
          );
        }
        const s = stop!;
        const kind = STOP[s.kind] ?? { label: s.kind, color: C.muted, title: s.kind };
        const minutes = (new Date(s.end_at).getTime() - new Date(s.start_at).getTime()) / 60_000;
        const end = sameDay(new Date(s.start_at), new Date(s.end_at))
          ? fmtTime.format(new Date(s.end_at))
          : when(s.end_at);
        const recent = Date.now() - new Date(s.end_at).getTime() < 30 * 60_000;
        return (
          <li key={`s${s.start_at}`}>
            <button
              type="button"
              className={p.row}
              title={`${when(s.start_at)} – ${end}`}
              onClick={() => {
                flyTo([s.lon, s.lat], 13);
                setData("selected", { type: "Point", coordinates: [s.lon, s.lat] });
              }}
            >
              <Glyph kind="square" color={kind.color} size={12} />
              <span className={p.main}>
                <span className={p.title}>{stopTitle(s)}</span>
                <span className={p.meta}>
                  {duration(minutes)}
                  {recent ? " · sigue ahí" : ""}
                </span>
              </span>
              <span className={p.time}>{when(s.start_at)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function CompanyRow({ c }: { c: CompanyRole }) {
  const since = c.since ? `desde ${month(c.since)}` : c.as_of ? `inspección de ${month(c.as_of)}` : null;
  const roles = c.roles.map((r) => ROLE[r] ?? r).join(", ");
  const fleet =
    c.sanctioned_vessels > 0 && c.vessels > 1 ? `${c.sanctioned_vessels} de ${c.vessels} barcos sancionados` : null;
  return (
    <li>
      <button
        type="button"
        className={p.row}
        onClick={() => navigate(`companies/${encodeURIComponent(c.id)}`)}
        title={[roles, since, c.sources.map((s) => SOURCE[s] ?? s).join(", "), fleet].filter(Boolean).join(" · ")}
      >
        <Glyph kind={c.sanctioned ? "dot" : "ring"} color={c.sanctioned ? C.critical : C.muted} size={13} />
        <span className={p.main}>
          <span className={p.title}>{c.name}</span>
          <span className={p.meta}>{[roles, fleet].filter(Boolean).join(" · ")}</span>
        </span>
      </button>
    </li>
  );
}

function outsideLinks(v: Vessel) {
  // Photos, registry data and port calls live there; linked rather than copied (their terms forbid it).
  const vf = `https://www.vesselfinder.com/vessels/details/${v.imo ?? v.mmsi}`;
  const mt = `https://www.marinetraffic.com/en/ais/details/ships/mmsi:${v.mmsi}`;
  return (
    <span style={{ display: "inline-flex", gap: 12 }}>
      <a className={p.link} href={vf} target="_blank" rel="noopener noreferrer">
        VesselFinder <ExternalLink size={10} />
      </a>
      <a className={p.link} href={mt} target="_blank" rel="noopener noreferrer">
        MarineTraffic <ExternalLink size={10} />
      </a>
    </span>
  );
}

function Alert({ color, title, children }: { color: string; title: string; children: React.ReactNode }) {
  return (
    <div
      className={p.alert}
      style={{ "--tint": `color-mix(in srgb, ${color} 10%, transparent)` } as React.CSSProperties}
    >
      <div className={p.alertTitle}>
        <Glyph kind="dot" color={color} size={12} />
        {title}
      </div>
      {children}
    </div>
  );
}

export function VesselDetail({ mmsi }: { mmsi: number }) {
  const vessel = useVessel(mmsi);
  const setLastVessel = useUi((u) => u.setLastVessel);
  const v = vessel.data;

  useEffect(() => {
    if (!v) return;
    setLastVessel({ mmsi: v.mmsi, name: v.name ?? `MMSI ${v.mmsi}` });
    const last = v.last_position?.geometry.coordinates as LngLat | undefined;
    if (v.track) fitBounds(boundsOf(v.track.coordinates), 11);
    else if (last) flyTo(last, 10);
    setData("selected", last ? { type: "Point", coordinates: last } : null);
    return () => setData("selected", null);
  }, [v, setLastVessel]);

  const crumbs: [string, (() => void) | null][] = [
    ["Barcos", () => navigate("vessels")],
    [v?.name ?? `MMSI ${mmsi}`, null],
  ];
  if (vessel.isPending || vessel.isError || !v)
    return (
      <div className={p.panel}>
        <Header crumbs={crumbs} />
        {vessel.isError ? <ErrorNote error={vessel.error} /> : <Loading />}
      </div>
    );

  const lp = v.last_position?.properties;
  const l = { sanctioned: v.sanction != null, risk: v.risk?.level ?? null, watched: v.watched.length > 0 };
  const listed = l.sanctioned
    ? "Sancionado"
    : l.risk === "sanctioned_owner"
      ? "Gestor sancionado"
      : l.risk
        ? "Vigilado"
        : null;

  return (
    <div className={p.panel}>
      <Header crumbs={crumbs} />
      <div className={p.scroll}>
        <div className={p.head}>
          <h1 className={p.headTitle}>{v.name ?? `MMSI ${v.mmsi}`}</h1>
          <div className={p.headMeta}>
            {[v.flag, v.ship_type, v.length_m && `${v.length_m} m`].filter(Boolean).map((f) => (
              <span key={String(f)}>{f}</span>
            ))}
            {listed && (
              <ListingLabel
                label={listed}
                color={l.sanctioned || l.risk === "sanctioned_owner" ? C.critical : C.serious}
              />
            )}
          </div>
        </div>
        {(v.sanction || v.risk) && (
          <div className={p.block}>
            {v.sanction && (
              <Alert color={C.critical} title="En listas de sanciones">
                <span>
                  Figura como {v.sanction.listed_as}
                  {v.sanction.programs ? ` (${v.sanction.programs.replaceAll(";", ", ")})` : ""}.
                </span>
              </Alert>
            )}
            {v.risk && (
              <Alert
                color={v.risk.level === "watchlisted" ? C.serious : C.critical}
                title={v.risk.level === "watchlisted" ? "En listas de vigilancia" : "Armador o gestor sancionado"}
              >
                {v.risk.reasons.map((r) => (
                  <span key={r}>{capitalise(r)}.</span>
                ))}
              </Alert>
            )}
          </div>
        )}
        <div className={p.block}>
          <WatchBox vessel={v} />
          <Properties
            rows={[
              ["MMSI", mono(v.mmsi)],
              ["IMO", v.imo ? mono(v.imo) : null],
              ["Indicativo", v.callsign ? mono(v.callsign) : null],
              ["Destino", v.destination],
              ["Manga", v.beam_m ? `${v.beam_m} m` : null],
              ["Última posición", lp ? ago(lp.received_at) : "sin posiciones"],
              ["Velocidad", lp?.sog != null ? mono(`${lp.sog} kn`) : null],
              ["Rumbo", lp?.cog != null ? mono(`${lp.cog}°`) : null],
              ["Derrota", v.track ? "últimas 24 h, en el mapa" : "sin derrota en 24 h"],
              ["Fuera", outsideLinks(v)],
            ]}
          />
        </div>
        <Group title="Propietario y gestores" count={v.companies.length}>
          {v.companies.length ? (
            <ul className={p.list}>
              {v.companies.map((c) => (
                <CompanyRow key={c.id} c={c} />
              ))}
            </ul>
          ) : (
            <Note>
              {v.imo
                ? "Las fuentes abiertas no dicen quién lo gestiona."
                : "Sin IMO no se puede saber quién lo gestiona."}
            </Note>
          )}
        </Group>
        {v.watchlists.length > 0 && (
          <Group title="Lo que dicen las listas" count={v.watchlists.length}>
            <div className={p.block}>
              {v.watchlists.map((w) => (
                <details key={w.watchlist}>
                  <summary style={{ color: "var(--text-2)" }}>{WATCHLIST[w.watchlist] ?? w.watchlist}</summary>
                  <p lang="en" style={{ margin: "6px 0 0", color: "var(--text-3)", fontSize: 12.5 }}>
                    {w.note ?? "Sin más detalle."}
                  </p>
                </details>
              ))}
            </div>
          </Group>
        )}
        <Group title="Escalas, fondeos y cruces" count={v.stops.length + v.transits.length}>
          {v.stops.length + v.transits.length ? <Voyage v={v} /> : <Note>Ninguna en 30 días.</Note>}
        </Group>
        <Group title="Eventos" count={v.events.length}>
          {v.events.length ? (
            <ul className={p.list}>
              {v.events.map((e) => {
                const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
                return (
                  <li key={e.id}>
                    <div className={p.row} title={e.message}>
                      <Glyph kind="diamond" color={sev.color} size={13} />
                      <span className={p.main}>
                        <span className={p.title}>{e.title.split(": ")[0]}</span>
                        <span className={p.meta}>{e.message}</span>
                      </span>
                      <span className={p.time}>{when(e.start_at)}</span>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <Note>Ninguno.</Note>
          )}
        </Group>
        <Group title="Visto por el radar" count={v.radar_sightings.length}>
          {v.radar_sightings.length ? (
            <ul className={p.list}>
              {v.radar_sightings.map((r) => (
                <li key={`${r.pass_id}${r.seen_at}`}>
                  <button type="button" className={p.row} onClick={() => navigate(`passes/${r.pass_id}`)}>
                    <Glyph kind={r.as_suspect ? "dot" : "ring"} color={r.as_suspect ? C.critical : C.blue} size={13} />
                    <span className={p.main}>
                      <span className={p.title}>{r.as_suspect ? "Sin AIS, donde pudo estar" : "Con su AIS"}</span>
                      {r.distance_m != null && <span className={p.meta}>a {metres(r.distance_m)}</span>}
                    </span>
                    <span className={p.time}>{when(r.seen_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <Note>Ninguna pasada lo ha captado todavía.</Note>
          )}
        </Group>
      </div>
    </div>
  );
}
