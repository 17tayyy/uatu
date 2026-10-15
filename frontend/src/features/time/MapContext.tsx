// The chip over the map saying which radar pass it shows, or why none.
import { Satellite } from "lucide-react";
import { usePass, useZones } from "../../api/queries";
import { navigate, useRoute } from "../../app/router";
import { useUi } from "../../app/store";
import { ALL } from "../../lib/constants";
import { when } from "../../lib/format";
import { passMoment } from "../../map/passes";
import s from "./time.module.css";

export function MapContext() {
  const route = useRoute();
  const passId = useUi((u) => u.passId);
  const note = useUi((u) => u.passNote);
  const zone = useUi((u) => u.zone);
  const t = useUi((u) => u.time?.t ?? null);
  const enterTime = useUi((u) => u.enterTime);
  const pass = usePass(passId).data;
  const zones = useZones().data?.zones ?? [];
  if (!passId && !note) return null;
  if (!pass) return note ? <div className={s.context}>{note}</div> : null;
  const onIt = route.section === "passes" && route.passId === pass.id;
  const moment = passMoment(pass);
  const zoneName = zones.find((z) => z.key === pass.zone)?.name ?? pass.zone;
  return (
    <div className={s.context}>
      <Satellite size={14} />
      <span>
        Radar · pasada de {when(pass.acquired_at)}
        {zone === ALL ? ` · ${zoneName}` : ""}
      </span>
      {t != null && Math.abs(t - moment) >= 1000 && (
        <button type="button" onClick={() => enterTime(moment)} title="Al segundo en que el radar miró la zona">
          Ir al instante
        </button>
      )}
      {!onIt && (
        <button type="button" onClick={() => navigate(`passes/${pass.id}`)}>
          Ver pasada
        </button>
      )}
    </div>
  );
}
