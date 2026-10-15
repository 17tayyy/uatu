// A vessel's AIS position at the moment of a pass, and whether the radar saw it.
import { Ship } from "lucide-react";
import { navigate } from "../../app/router";
import type { Selection } from "../../app/store";
import { C } from "../../lib/constants";
import { Button, Glyph, mono, Properties } from "../../ui";
import { InspectorHeader } from "./Inspector";
import s from "./inspector.module.css";

type PassAisSelection = Extract<Selection, { kind: "passAis" }>;

export function PassAisInspector({ selection }: { selection: PassAisSelection }) {
  const a = selection.ais;
  return (
    <>
      <InspectorHeader
        crumbs={[
          ["Pasada", () => navigate(`passes/${selection.passId}`)],
          ["AIS", null],
        ]}
      />
      <div className={s.body}>
        <div className={s.head}>
          <div className={s.kind}>
            <Glyph kind="ring" color={C.text2} size={13} />
            {a.status === "detected" ? "Visto por el radar" : "No visto por el radar"}
          </div>
          <h2 className={s.title}>{a.name ?? `MMSI ${a.mmsi}`}</h2>
          <p className={s.text}>Su posición AIS, interpolada al instante en que el radar miró ese punto.</p>
        </div>
        <div className={s.section}>
          <Properties
            rows={[
              ["MMSI", mono(a.mmsi)],
              ["Eslora", a.length_m != null ? mono(`${a.length_m} m`) : null],
              ["Velocidad", a.sog != null ? mono(`${a.sog} kn`) : null],
            ]}
          />
          <div className={s.actions}>
            <Button size="small" onClick={() => navigate(`vessels/${a.mmsi}`)}>
              <Ship size={13} /> Barco
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}
