// The AIS events of the last day to week: gaps, encounters, loitering, sanctioned vessels...
import { useMemo } from "react";
import { useEvents } from "../../api/queries";
import type { EventFeature } from "../../api/types";
import { type EventFilter, useUi } from "../../app/store";
import { SEVERITY } from "../../lib/constants";
import { dayHeading, when } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { flyTo } from "../../map/instance";
import { selectEvent } from "../../map/interactions";
import { Empty, ErrorNote, Loading, Segmented, Tag } from "../../ui";
import p from "../panel.module.css";

function EventRow({ feature }: { feature: EventFeature }) {
  const e = feature.properties;
  const selected = useUi((u) => u.selection?.kind === "event" && u.selection.event.id === e.id);
  const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
  const lngLat = feature.geometry.coordinates as LngLat;
  return (
    <li>
      <button
        type="button"
        className={p.row}
        aria-current={selected || undefined}
        onClick={() => {
          selectEvent(e, lngLat);
          flyTo(lngLat, 10);
        }}
      >
        <div className={p.rowTop}>
          <Tag shape="diamond" color={sev.color}>
            {sev.label}
          </Tag>
          <span className={p.grow} />
          <span className={p.rowTime}>{when(e.start_at)}</span>
        </div>
        <div style={{ fontWeight: 600 }}>{e.title}</div>
        <div className={p.rowText}>{e.message}</div>
      </button>
    </li>
  );
}

export function EventList() {
  const zone = useUi((u) => u.zone);
  const filter = useUi((u) => u.eventFilter);
  const setFilter = useUi((u) => u.setEventFilter);
  const events = useEvents(zone, filter.hours, filter.minSeverity);

  const days = useMemo(() => {
    const sorted = [...(events.data?.features ?? [])].sort((a, b) =>
      b.properties.start_at.localeCompare(a.properties.start_at),
    );
    const groups: [string, EventFeature[]][] = [];
    for (const f of sorted) {
      const day = dayHeading(f.properties.start_at);
      const last = groups.at(-1);
      if (last && last[0] === day) last[1].push(f);
      else groups.push([day, [f]]);
    }
    return groups;
  }, [events.data]);

  return (
    <>
      <div className={p.header}>
        <div className={p.eyebrow}>AIS</div>
        <h1 className={p.title}>Eventos</h1>
        <div className={p.subtitle}>
          Apagones, encuentros, merodeos, barcos sobre cables, sancionados y lo que el AIS no puede decir de verdad.
        </div>
      </div>
      <div className={p.toolbar}>
        <Segmented<EventFilter["minSeverity"]>
          label="Gravedad"
          options={[
            [3, "Alertas"],
            [2, "Notables"],
            [1, "Todos"],
          ]}
          value={filter.minSeverity}
          onChange={(minSeverity) => setFilter({ minSeverity })}
        />
        <span className={p.grow} />
        <Segmented<EventFilter["hours"]>
          label="Periodo"
          options={[
            [24, "24 h"],
            [48, "48 h"],
            [168, "7 d"],
          ]}
          value={filter.hours}
          onChange={(hours) => setFilter({ hours })}
        />
      </div>
      {events.isPending ? (
        <Loading />
      ) : events.isError ? (
        <ErrorNote error={events.error} />
      ) : days.length === 0 ? (
        <Empty>Ningún evento con este filtro.</Empty>
      ) : (
        days.map(([day, list]) => (
          <section key={day}>
            <h2 className={p.dayHeading}>{day}</h2>
            <ul className={p.list}>
              {list.map((f) => (
                <EventRow key={f.properties.id} feature={f} />
              ))}
            </ul>
          </section>
        ))
      )}
    </>
  );
}
