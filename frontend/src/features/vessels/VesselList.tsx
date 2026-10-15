// The vessels heard in the last hour (sanctioned and watched first), and a search over all ever heard.
import { Search } from "lucide-react";
import { useDeferredValue, useMemo } from "react";
import { useLive, useVesselSearch } from "../../api/queries";
import type { LiveVesselFeature } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { ago } from "../../lib/format";
import { listing } from "../../lib/listing";
import { Empty, ErrorNote, Loading, Section, Tag } from "../../ui";
import p from "../panel.module.css";

function LiveRow({ feature }: { feature: LiveVesselFeature }) {
  const v = feature.properties;
  const l = listing(v);
  const moving = v.sog != null && v.sog >= 0.5;
  const facts = [v.flag, v.ship_type, v.length_m && `${v.length_m} m`].filter(Boolean).join(" · ");
  return (
    <li>
      <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
        <div className={p.rowTop}>
          <span className={p.rowTitle}>{v.name ?? `MMSI ${v.mmsi}`}</span>
          {l.label && (
            <Tag shape="arrow" color={l.color}>
              {l.label}
            </Tag>
          )}
          <span className={p.rowTime}>{moving ? `${v.sog} nudos` : "parado"}</span>
        </div>
        {facts && <div className={p.rowMeta}>{facts}</div>}
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

  return (
    <>
      <div className={p.header}>
        <div className={p.eyebrow}>AIS</div>
        <h1 className={p.title}>Barcos</h1>
      </div>
      <div className={p.toolbar}>
        <div className={p.searchWrap}>
          <Search size={15} className={p.searchIcon} />
          <input
            className={p.search}
            type="search"
            placeholder="Buscar por nombre o MMSI"
            aria-label="Buscar barco"
            value={query}
            maxLength={40}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      {q.length >= 2 ? (
        search.isPending ? (
          <Loading />
        ) : search.isError ? (
          <ErrorNote error={search.error} />
        ) : search.data.length === 0 ? (
          <Empty>Ningún barco coincide.</Empty>
        ) : (
          <ul className={p.list} style={{ paddingTop: 8 }}>
            {search.data.map((v) => (
              <li key={v.mmsi}>
                <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
                  <div className={p.rowTop}>
                    <span className={p.rowTitle}>{v.name ?? `MMSI ${v.mmsi}`}</span>
                    <span className={p.rowTime}>{v.mmsi}</span>
                  </div>
                  <div className={p.rowMeta}>
                    {[v.flag, v.ship_type, v.last_seen_at && `visto ${ago(v.last_seen_at)}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : (
        <Section title="En la zona en la última hora" count={sorted.length}>
          {live.isPending ? (
            <Loading />
          ) : sorted.length === 0 ? (
            <Empty>Ningún barco oído en la última hora.</Empty>
          ) : (
            <ul className={p.list}>
              {sorted.map((f) => (
                <LiveRow key={f.properties.mmsi} feature={f} />
              ))}
            </ul>
          )}
        </Section>
      )}
    </>
  );
}
