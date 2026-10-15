// A vessel's card: who it is, how it stands with sanctions, who runs it, where it stopped, what it did.
import { ArrowLeft, ExternalLink } from "lucide-react";
import { useEffect } from "react";
import { useVessel } from "../../api/queries";
import type { CompanyRole, StopOut, TransitOut, VesselDetail as Vessel } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, COUNTRY, ROLE, SEVERITY, SOURCE, STOP, WATCHLIST } from "../../lib/constants";
import { ago, capitalise, duration, fmtTime, metres, month, sameDay, when } from "../../lib/format";
import { boundsOf, type LngLat } from "../../lib/geo";
import { fitBounds, flyTo, setData } from "../../map/instance";
import { Callout, ErrorNote, KeyValues, Loading, Note, Section, Shape, Tag } from "../../ui";
import p from "../panel.module.css";
import { WatchBox } from "../watch/WatchBox";

function stopTitle(s: StopOut): string {
  const abroad = s.place_country && !["Spain", s.place].includes(s.place_country); // not "Gibraltar (Gibraltar)"
  const where = abroad ? `${s.place} (${COUNTRY[s.place_country!] ?? s.place_country})` : s.place;
  if (!where) return STOP[s.kind]?.title ?? s.kind;
  if (s.kind === "berth") return `Puerto de ${where}`;
  if (s.kind === "anchorage") return `Fondeado frente a ${where}`;
  return `Parado mar adentro, a ${Math.round(s.place_km ?? 0)} km de ${where}`;
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
                <div className={p.rowTop}>
                  <span className={p.rowTitle}>
                    Cruza el Estrecho hacia {transit.direction === "east" ? "el Mediterráneo" : "el Atlántico"}
                  </span>
                  <span className={p.rowTime}>{when(transit.at)}</span>
                </div>
              </div>
            </li>
          );
        }
        const s = stop!;
        const kind = STOP[s.kind] ?? { label: s.kind, color: C.muted };
        const minutes = (new Date(s.end_at).getTime() - new Date(s.start_at).getTime()) / 60_000;
        const span = `${when(s.start_at)} – ${sameDay(new Date(s.start_at), new Date(s.end_at)) ? fmtTime.format(new Date(s.end_at)) : when(s.end_at)}`;
        const recent = Date.now() - new Date(s.end_at).getTime() < 30 * 60_000;
        return (
          <li key={`s${s.start_at}`}>
            <button
              type="button"
              className={p.row}
              onClick={() => {
                flyTo([s.lon, s.lat], 13);
                setData("selected", { type: "Point", coordinates: [s.lon, s.lat] });
              }}
            >
              <div className={p.rowTop}>
                <span className={p.rowTitle}>{stopTitle(s)}</span>
                <Tag shape="square" color={kind.color}>
                  {kind.label}
                </Tag>
              </div>
              <div className={p.rowMeta}>
                {span} · {duration(minutes)}
                {recent ? " · sigue ahí" : ""}
              </div>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function CompanyRow({ c }: { c: CompanyRole }) {
  const since = c.since ? `desde ${month(c.since)}` : c.as_of ? `inspección de ${month(c.as_of)}` : null;
  const facts = [c.roles.map((r) => ROLE[r] ?? r).join(", "), since, c.sources.map((s) => SOURCE[s] ?? s).join(", ")];
  const fleet =
    c.sanctioned_vessels > 0 && c.vessels > 1
      ? `${c.sanctioned_vessels} de sus ${c.vessels} barcos conocidos, sancionados`
      : null;
  return (
    <li>
      <button type="button" className={p.row} onClick={() => navigate(`companies/${encodeURIComponent(c.id)}`)}>
        <div className={p.rowTop}>
          <span className={p.rowTitle}>{c.name}</span>
          {c.sanctioned && (
            <Tag shape="square" color={C.critical}>
              {c.kind === "person" ? "Sancionado" : "Sancionada"}
            </Tag>
          )}
        </div>
        <div className={p.rowMeta}>{facts.filter(Boolean).join(" · ")}</div>
        {fleet && <div className={p.rowMeta}>{fleet}</div>}
      </button>
    </li>
  );
}

export function VesselDetail({ mmsi }: { mmsi: number }) {
  const vessel = useVessel(mmsi);
  const setLastVessel = useUi((u) => u.setLastVessel);
  const v = vessel.data;

  // Its track in view, its last position marked.
  useEffect(() => {
    if (!v) return;
    setLastVessel({ mmsi: v.mmsi, name: v.name ?? `MMSI ${v.mmsi}` });
    const last = v.last_position?.geometry.coordinates as LngLat | undefined;
    if (v.track) fitBounds(boundsOf(v.track.coordinates), 11);
    else if (last) flyTo(last, 10);
    setData("selected", last ? { type: "Point", coordinates: last } : null);
    return () => setData("selected", null);
  }, [v, setLastVessel]);

  if (vessel.isPending) return <Loading>Cargando barco…</Loading>;
  if (vessel.isError) return <ErrorNote error={vessel.error} />;
  if (!v) return null;
  const lp = v.last_position?.properties;
  const facts = [v.flag, v.ship_type, v.length_m && `${v.length_m} m`].filter(Boolean).join(" · ");

  return (
    <>
      <button type="button" className={p.back} onClick={() => navigate("vessels")}>
        <ArrowLeft size={14} /> Barcos
      </button>
      <div className={p.header}>
        <div className={p.eyebrow}>
          MMSI {v.mmsi}
          {v.imo ? ` · IMO ${v.imo}` : ""}
        </div>
        <h1 className={p.title}>{v.name ?? `MMSI ${v.mmsi}`}</h1>
        {facts && <div className={p.subtitle}>{facts}</div>}
      </div>
      {
        <div className={p.callouts}>
          {v.sanction && (
            <Callout tone={C.critical} icon={<Shape kind="arrow" color={C.critical} />} title="En listas de sanciones">
              <span>
                Figura como {v.sanction.listed_as}
                {v.sanction.programs ? ` (${v.sanction.programs.replaceAll(";", ", ")})` : ""}. Fuente: OpenSanctions.
              </span>
            </Callout>
          )}
          {v.risk && (
            <Callout
              tone={v.risk.level === "watchlisted" ? C.serious : C.critical}
              icon={<Shape kind="arrow" color={v.risk.level === "watchlisted" ? C.serious : C.critical} />}
              title={v.risk.level === "watchlisted" ? "En listas de vigilancia" : "Armador o gestor sancionado"}
            >
              {v.risk.level !== "watchlisted" && <span>No figura en listas de sanciones, pero:</span>}
              <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
                {v.risk.reasons.map((r) => (
                  <li key={r}>{capitalise(r)}</li>
                ))}
              </ul>
              <span>Fuente: OpenSanctions.</span>
            </Callout>
          )}
          <WatchBox vessel={v} />
        </div>
      }
      <div className={p.block}>
        <KeyValues
          rows={[
            ["Indicativo", v.callsign],
            ["Destino", v.destination],
            ["Manga", v.beam_m ? `${v.beam_m} m` : null],
            ["Última posición", lp ? ago(lp.received_at) : "sin posiciones"],
            ["Velocidad", lp?.sog != null ? `${lp.sog} nudos` : null],
            ["Rumbo", lp?.cog != null ? `${lp.cog}°` : null],
            ["Derrota", v.track ? "últimas 24 h en el mapa" : "sin derrota en 24 h"],
          ]}
        />
      </div>
      {/* Photos, registry data and port calls live there; linked rather than copied (their terms forbid it). */}
      <div className={p.links}>
        <a
          href={`https://www.vesselfinder.com/vessels/details/${v.imo ?? v.mmsi}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          VesselFinder <ExternalLink size={11} />
        </a>
        <a
          href={`https://www.marinetraffic.com/en/ais/details/ships/mmsi:${v.mmsi}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          MarineTraffic <ExternalLink size={11} />
        </a>
      </div>
      <Section title="Propietario y gestores" count={v.companies.length}>
        {v.companies.length ? (
          <ul className={p.list}>
            {v.companies.map((c) => (
              <CompanyRow key={c.id} c={c} />
            ))}
          </ul>
        ) : (
          <Note>
            {v.imo
              ? "Las fuentes abiertas no dicen quién lo gestiona: solo cubren barcos sancionados, vigilados o detenidos."
              : "Sin IMO no se puede saber quién lo gestiona."}
          </Note>
        )}
      </Section>
      {v.watchlists.length > 0 && (
        <Section title="Lo que dicen las listas">
          <div className={p.block}>
            {v.watchlists.map((w) => (
              <details key={w.watchlist}>
                <summary style={{ cursor: "pointer" }}>{WATCHLIST[w.watchlist] ?? w.watchlist}</summary>
                <p lang="en" style={{ margin: "6px 0 0", color: "var(--text-3)", fontSize: 13 }}>
                  {w.note ?? "Sin más detalle."}
                </p>
              </details>
            ))}
          </div>
        </Section>
      )}
      <Section title="Escalas, fondeos y cruces del Estrecho" count={v.stops.length + v.transits.length}>
        {v.stops.length + v.transits.length ? (
          <Voyage v={v} />
        ) : (
          <Note>Ninguna parada ni cruce del Estrecho en 30 días, en las zonas analizadas.</Note>
        )}
      </Section>
      <Section title="Eventos" count={v.events.length}>
        {v.events.length ? (
          <ul className={p.list}>
            {v.events.map((e) => {
              const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
              return (
                <li key={e.id}>
                  <div className={p.row}>
                    <div className={p.rowTop}>
                      <Tag shape="diamond" color={sev.color}>
                        {sev.label}
                      </Tag>
                      <span className={p.grow} />
                      <span className={p.rowTime}>{when(e.start_at)}</span>
                    </div>
                    <div className={p.rowText}>{e.message}</div>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <Note>Ningún evento registrado.</Note>
        )}
      </Section>
      <Section title="Visto por el radar" count={v.radar_sightings.length}>
        {v.radar_sightings.length ? (
          <ul className={p.list}>
            {v.radar_sightings.map((r) => (
              <li key={`${r.pass_id}${r.seen_at}`}>
                <button type="button" className={p.row} onClick={() => navigate(`passes/${r.pass_id}`)}>
                  <div className={p.rowTop}>
                    <span className={p.rowTitle}>{when(r.seen_at)}</span>
                    {r.as_suspect && <Tag color={C.critical}>Sin AIS</Tag>}
                  </div>
                  <div className={p.rowMeta}>
                    {r.as_suspect
                      ? "Detección sin AIS donde pudo estar tras dejar de emitir"
                      : "Detección emparejada con su AIS"}
                    {r.distance_m != null ? ` · a ${metres(r.distance_m)}` : ""}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Note>Ninguna pasada lo ha captado todavía.</Note>
        )}
      </Section>
    </>
  );
}
