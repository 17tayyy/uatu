// An AIS event in full, with the radar pass of its moment if a satellite passed then.
import { History, Satellite, Ship } from "lucide-react";
import { navigate } from "../../app/router";
import { type Selection, useUi } from "../../app/store";
import { SEVERITY } from "../../lib/constants";
import { when } from "../../lib/format";
import { flyTo } from "../../map/instance";
import { passForEvent } from "../../map/passes";
import { Button, Glyph, mono, Properties } from "../../ui";
import { InspectorHeader } from "./Inspector";
import s from "./inspector.module.css";

type EventSelection = Extract<Selection, { kind: "event" }>;

export function EventInspector({ selection }: { selection: EventSelection }) {
  const e = selection.event;
  const enterTime = useUi((u) => u.enterTime);
  const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
  const pass = passForEvent(e, selection.lngLat);
  const [what, who] = e.title.includes(": ") ? e.title.split(/: (.*)/s) : [e.title, ""];
  return (
    <>
      <InspectorHeader
        crumbs={[
          ["Eventos", () => navigate("events")],
          [what ?? "Evento", null],
        ]}
      />
      <div className={s.body}>
        <div className={s.head}>
          <div className={s.kind}>
            <Glyph kind="diamond" color={sev.color} size={13} />
            {sev.label} · {what}
          </div>
          <h2 className={s.title}>{who || what}</h2>
          <p className={s.text}>{e.message}</p>
        </div>
        <div className={s.section}>
          <Properties
            rows={[
              ["Desde", when(e.start_at)],
              ["Hasta", e.end_at ? when(e.end_at) : "sigue"],
              ["Satélite", pass ? `pasó ${when(pass.acquired_at)}` : "no pasó durante el evento"],
              ["MMSI", mono(e.mmsi)],
            ]}
          />
        </div>
        <div className={s.section}>
          <div className={s.actions}>
            <Button size="small" onClick={() => navigate(`vessels/${e.mmsi}`)}>
              <Ship size={13} /> Barco
            </Button>
            {pass && (
              <Button size="small" onClick={() => navigate(`passes/${pass.id}`)}>
                <Satellite size={13} /> Pasada
              </Button>
            )}
            <Button
              size="small"
              onClick={() => {
                enterTime(new Date(e.start_at).getTime() - 20 * 60_000);
                flyTo(selection.lngLat, 11);
              }}
              title="Desde un poco antes de que empezara"
            >
              <History size={13} /> Reproducir
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
