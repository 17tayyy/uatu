// A vessel's AIS position at the moment of a pass, and whether the radar saw it.
import { Ship } from "lucide-react";
import { navigate } from "../../app/router";
import type { Selection } from "../../app/store";
import { C } from "../../lib/constants";
import { Button, KeyValues, Tag } from "../../ui";
import s from "./inspector.module.css";

type PassAisSelection = Extract<Selection, { kind: "passAis" }>;

export function PassAisInspector({ selection }: { selection: PassAisSelection }) {
  const a = selection.ais;
  return (
    <div className={s.body}>
      <div className={s.head}>
        <Tag shape="ring" color={C.text2}>
          {a.status === "detected" ? "Visto por el radar" : "No visto por el radar"}
        </Tag>
        <h2 className={s.title}>{a.name ?? `MMSI ${a.mmsi}`}</h2>
      </div>
      <p className={s.text}>Su posición AIS, interpolada al instante en que el radar miró ese punto.</p>
      <KeyValues
        rows={[
          ["Eslora", a.length_m != null ? `${a.length_m} m` : null],
          ["Velocidad", a.sog != null ? `${a.sog} nudos` : null],
        ]}
      />
      <div className={s.actions}>
        <Button size="small" onClick={() => navigate(`vessels/${a.mmsi}`)}>
          <Ship size={13} /> Ver barco
        </Button>
      </div>
    </div>
  );
}
