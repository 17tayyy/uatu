// The panel's top: the zone looked at, and whether Uatu is listening right now.
import { useStatus, useZones } from "../../api/queries";
import { useUi } from "../../app/store";
import { ALL } from "../../lib/constants";
import { ago, plural } from "../../lib/format";
import s from "./status.module.css";

export function StatusBar() {
  const zone = useUi((u) => u.zone);
  const setZone = useUi((u) => u.setZone);
  const zones = useZones().data?.zones ?? [];
  const status = useStatus(zone);
  const st = status.data;
  const fresh = st?.last_ais_at != null && Date.now() - new Date(st.last_ais_at).getTime() < 10 * 60_000;
  const live = Boolean(st?.recording && fresh);
  return (
    <header className={s.bar}>
      <div className={s.top}>
        <span className={s.brand}>Uatu</span>
        <select className={s.zone} value={zone} onChange={(e) => setZone(e.target.value)} aria-label="Zona">
          <option value={ALL}>Toda la costa</option>
          {zones.map((z) => (
            <option key={z.key} value={z.key}>
              {z.name}
            </option>
          ))}
        </select>
      </div>
      <div className={s.line}>
        {status.isError ? (
          <span className={s.state} data-tone="critical">
            <span className={s.dot} /> Sin conexión con el servidor
          </span>
        ) : st ? (
          <>
            <span className={s.state} data-tone={live ? "good" : "critical"}>
              <span className={s.dot} />
              {live ? "Grabando AIS" : "AIS detenido"}
            </span>
            {st.last_ais_at && <span>· {ago(st.last_ais_at)}</span>}
            <span>· {plural(st.vessels_last_hour, "barco", "barcos")}/h</span>
            <span>· {plural(st.events_24h, "evento", "eventos")}/24 h</span>
          </>
        ) : (
          <span>…</span>
        )}
      </div>
    </header>
  );
}
