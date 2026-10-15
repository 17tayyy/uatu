// The right-hand panel over the map: whatever was picked on the map or in a list, in full.
import { X } from "lucide-react";
import { useUi } from "../../app/store";
import { DetectionInspector } from "./DetectionInspector";
import { EventInspector } from "./EventInspector";
import s from "./inspector.module.css";
import { OpticalCard } from "./Optical";
import { PassAisInspector } from "./PassAisInspector";

export function Inspector() {
  const selection = useUi((u) => u.selection);
  const select = useUi((u) => u.select);
  return (
    <>
      <OpticalCard />
      {selection && (
        <aside className={s.inspector} aria-label="Detalle">
          <button
            type="button"
            className={s.close}
            onClick={() => select(null)}
            aria-label="Cerrar"
            title="Cerrar (Esc)"
          >
            <X size={16} />
          </button>
          {selection.kind === "detection" && <DetectionInspector key={selection.id} selection={selection} />}
          {selection.kind === "event" && <EventInspector key={selection.event.id} selection={selection} />}
          {selection.kind === "passAis" && <PassAisInspector key={selection.ais.mmsi} selection={selection} />}
        </aside>
      )}
    </>
  );
}
