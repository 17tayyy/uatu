// An AIS event in full, with the radar pass of its moment if a satellite passed then.
import { History, Satellite, Ship } from "lucide-react";
import { navigate } from "../../app/router";
import { type Selection, useUi } from "../../app/store";
import { SEVERITY } from "../../lib/constants";
import { when } from "../../lib/format";
import { flyTo } from "../../map/instance";
import { passForEvent } from "../../map/passes";
import { Button, KeyValues, Tag } from "../../ui";
import s from "./inspector.module.css";

type EventSelection = Extract<Selection, { kind: "event" }>;

export function EventInspector({ selection }: { selection: EventSelection }) {
  const e = selection.event;
  const enterTime = useUi((u) => u.enterTime);
  const select = useUi((u) => u.select);
  const sev = SEVERITY[e.severity] ?? SEVERITY[1]!;
  const pass = passForEvent(e, selection.lngLat);
  return (
    <div className={s.body}>
      <div className={s.head}>
        <Tag shape="diamond" color={sev.color}>
          {sev.label}
        </Tag>
        <h2 className={s.title}>{e.title}</h2>
      </div>
      <p className={s.text}>{e.message}</p>
      <KeyValues
        rows={[
          ["Desde", when(e.start_at)],
          ["Hasta", e.end_at ? when(e.end_at) : "sigue"],
          ["Satélite", pass ? `pasó ${when(pass.acquired_at)}` : "no pasó por aquí durante el evento"],
        ]}
      />
      <div className={s.actions}>
        <Button size="small" onClick={() => navigate(`vessels/${e.mmsi}`)}>
          <Ship size={13} /> Ver barco
        </Button>
        {pass && (
          <Button size="small" onClick={() => navigate(`passes/${pass.id}`)}>
            <Satellite size={13} /> Ver pasada
          </Button>
        )}
        <Button
          size="small"
          onClick={() => {
            enterTime(new Date(e.start_at).getTime() - 20 * 60_000);
            flyTo(selection.lngLat, 11);
            select({ ...selection });
          }}
          title="Reproducir desde un poco antes de que empezara"
        >
          <History size={13} /> Reproducir
        </Button>
      </div>
    </div>
  );
}
