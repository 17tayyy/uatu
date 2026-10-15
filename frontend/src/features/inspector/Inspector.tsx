// The panel docked right of the map: whatever was picked on the map or in a list, in full.
import { ChevronDown, ChevronUp, X } from "lucide-react";
import type { ReactNode } from "react";
import { useUi } from "../../app/store";
import { Button, Header } from "../../ui";
import { DetectionInspector } from "./DetectionInspector";
import { EventInspector } from "./EventInspector";
import s from "./inspector.module.css";
import { PassAisInspector } from "./PassAisInspector";

/** The inspector's bar: where the thing belongs, moving along the list being reviewed, closing. */
export function InspectorHeader({
  crumbs,
  position,
}: {
  crumbs: [string, (() => void) | null][];
  position?: { at: number; of: number; prev: () => void; next: () => void } | null;
}) {
  const select = useUi((u) => u.select);
  const actions: ReactNode = (
    <>
      {position && (
        <>
          <span className="mono" style={{ color: "var(--text-4)", fontSize: 11.5, marginRight: 4 }}>
            {position.at + 1}/{position.of}
          </span>
          <Button
            icon
            size="small"
            variant="ghost"
            disabled={position.at === 0}
            onClick={position.prev}
            title="Anterior (K)"
          >
            <ChevronUp size={14} />
          </Button>
          <Button
            icon
            size="small"
            variant="ghost"
            disabled={position.at === position.of - 1}
            onClick={position.next}
            title="Siguiente (J)"
          >
            <ChevronDown size={14} />
          </Button>
        </>
      )}
      <Button icon size="small" variant="ghost" onClick={() => select(null)} title="Cerrar (Esc)" aria-label="Cerrar">
        <X size={14} />
      </Button>
    </>
  );
  return <Header crumbs={crumbs} actions={actions} />;
}

export function Inspector() {
  const selection = useUi((u) => u.selection);
  if (!selection) return null;
  return (
    <aside className={s.inspector} aria-label="Detalle">
      {selection.kind === "detection" && <DetectionInspector key={selection.id} selection={selection} />}
      {selection.kind === "event" && <EventInspector key={selection.event.id} selection={selection} />}
      {selection.kind === "passAis" && <PassAisInspector key={selection.ais.mmsi} selection={selection} />}
    </aside>
  );
}
