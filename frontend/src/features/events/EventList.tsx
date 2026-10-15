// The AIS events of the last day to week, by day.
import { useMemo } from "react";
import { useEvents } from "../../api/queries";
import type { EventFeature } from "../../api/types";
import { type EventFilter, useUi } from "../../app/store";
import { SEVERITY } from "../../lib/constants";
import { dayHeading, fmtTime } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { flyTo } from "../../map/instance";
import { selectEvent } from "../../map/interactions";
import { Empty, ErrorNote, Glyph, Group, Header, Loading, ToggleGroup, Toolbar } from "../../ui";
import p from "../panel.module.css";

function EventRow({ feature }: { feature: EventFeature }) {
  const e = feature.properties;
  const selected = useUi((u) => u.selection?.kind === "event" && u.selection.event.id === e.id);
  const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
  const lngLat = feature.geometry.coordinates as LngLat;
  const [what, who] = e.title.includes(": ") ? e.title.split(/: (.*)/s) : [e.title, ""];
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
        title={e.message}
      >
        <Glyph kind="diamond" color={sev.color} title={sev.label} />
        <span className={p.main}>
          <span className={p.title}>{who || what}</span>
          {who && <span className={p.meta}>{what}</span>}
        </span>
        <span className={p.time}>{fmtTime.format(new Date(e.start_at))}</span>
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
    <div className={p.panel}>
      <Header crumbs={[["Eventos", null]]} />
      <Toolbar>
        <ToggleGroup<EventFilter["minSeverity"]>
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
        <ToggleGroup<EventFilter["hours"]>
          label="Periodo"
          options={[
            [24, "24 h"],
            [48, "48 h"],
            [168, "7 d"],
          ]}
          value={filter.hours}
          onChange={(hours) => setFilter({ hours })}
        />
      </Toolbar>
      <div className={p.scroll}>
        {events.isPending ? (
          <Loading />
        ) : events.isError ? (
          <ErrorNote error={events.error} />
        ) : days.length === 0 ? (
          <Empty>Ningún evento con este filtro.</Empty>
        ) : (
          days.map(([day, list]) => (
            <Group key={day} title={day} count={list.length}>
              <ul className={p.list}>
                {list.map((f) => (
                  <EventRow key={f.properties.id} feature={f} />
                ))}
              </ul>
            </Group>
          ))
        )}
      </div>
    </div>
  );
}
