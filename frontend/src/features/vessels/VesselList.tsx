// The vessels heard in the last hour (sanctioned and watched first), and a search over all ever heard.
import { Search } from "lucide-react";
import { useDeferredValue, useMemo } from "react";
import { useLive, useVesselSearch } from "../../api/queries";
import type { LiveVesselFeature } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C } from "../../lib/constants";
import { ago } from "../../lib/format";
import { listing } from "../../lib/listing";
import { Empty, ErrorNote, Glyph, Group, Header, Loading, Toolbar } from "../../ui";
import p from "../panel.module.css";
import s from "./vessels.module.css";

export function ListingLabel({ label, color }: { label: string | null; color: string }) {
  if (!label) return null;
  return (
    <span className={s.label}>
      <span className={s.labelDot} style={{ background: color }} />
      {label}
    </span>
  );
}

function LiveRow({ feature }: { feature: LiveVesselFeature }) {
  const v = feature.properties;
  const l = listing(v);
  const moving = v.sog != null && v.sog >= 0.5;
  const meta = [v.flag, v.ship_type, v.length_m && `${v.length_m} m`].filter(Boolean).join(" · ");
  return (
    <li>
      <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
        <Glyph kind={moving ? "arrow" : "ring"} color={l.level ? l.color : C.muted} size={13} />
        <span className={p.main}>
          <span className={p.title}>{v.name ?? `MMSI ${v.mmsi}`}</span>
          <span className={p.meta}>{meta}</span>
        </span>
        <ListingLabel label={l.label} color={l.color} />
        <span className={p.time}>{moving ? `${v.sog} kn` : "parado"}</span>
      </button>
    </li>
  );
}

export function VesselList() {
  const zone = useUi((u) => u.zone);
  const query = useUi((u) => u.vesselQuery);
  const setQuery = useUi((u) => u.setVesselQuery);
  const q = useDeferredValue(query.trim());
  const live = useLive(zone);
  const search = useVesselSearch(q);

  const sorted = useMemo(
    () =>
      [...(live.data?.features ?? [])].sort(
        (a, b) =>
          listing(b.properties).level - listing(a.properties).level ||
          Number(a.properties.name == null) - Number(b.properties.name == null) ||
          (a.properties.name ?? String(a.properties.mmsi)).localeCompare(
            b.properties.name ?? String(b.properties.mmsi),
          ),
      ),
    [live.data],
  );
  const flagged = sorted.filter((f) => listing(f.properties).level > 0);
  const rest = sorted.filter((f) => listing(f.properties).level === 0);

  return (
    <div className={p.panel}>
      <Header crumbs={[["Barcos", null]]} />
      <Toolbar>
        <label className={p.searchField}>
          <Search size={14} />
          <input
            type="search"
            placeholder="Buscar por nombre o MMSI"
            aria-label="Buscar barco"
            value={query}
            maxLength={40}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </Toolbar>
      <div className={p.scroll}>
        {q.length >= 2 ? (
          search.isPending ? (
            <Loading />
          ) : search.isError ? (
            <ErrorNote error={search.error} />
          ) : search.data.length === 0 ? (
            <Empty>Ningún barco coincide.</Empty>
          ) : (
            <Group title="Resultados" count={search.data.length}>
              <ul className={p.list}>
                {search.data.map((v) => (
                  <li key={v.mmsi}>
                    <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
                      <Glyph kind="ring" color={C.muted} size={13} />
                      <span className={p.main}>
                        <span className={p.title}>{v.name ?? `MMSI ${v.mmsi}`}</span>
                        <span className={p.meta}>{[v.flag, v.ship_type].filter(Boolean).join(" · ")}</span>
                      </span>
                      <span className={p.time}>{v.last_seen_at ? ago(v.last_seen_at) : v.mmsi}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </Group>
          )
        ) : live.isPending ? (
          <Loading />
        ) : sorted.length === 0 ? (
          <Empty>Ningún barco oído en la última hora.</Empty>
        ) : (
          <>
            {flagged.length > 0 && (
              <Group title="Sancionados y vigilados" count={flagged.length}>
                <ul className={p.list}>
                  {flagged.map((f) => (
                    <LiveRow key={f.properties.mmsi} feature={f} />
                  ))}
                </ul>
              </Group>
            )}
            <Group title="Oídos en la última hora" count={rest.length}>
              <ul className={p.list}>
                {rest.map((f) => (
                  <LiveRow key={f.properties.mmsi} feature={f} />
                ))}
              </ul>
            </Group>
          </>
        )}
      </div>
    </div>
  );
}
