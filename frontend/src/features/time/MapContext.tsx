// Over the map, top left: which radar pass it shows, or why none.
import { usePass, useZones } from "../../api/queries";
import { navigate, useRoute } from "../../app/router";
import { useUi } from "../../app/store";
import { C } from "../../lib/constants";
import { fmtTime, when } from "../../lib/format";
import { passMoment } from "../../map/passes";
import { Glyph } from "../../ui";
import s from "./time.module.css";

export function MapContext() {
  const route = useRoute();
  const passId = useUi((u) => u.passId);
  const note = useUi((u) => u.passNote);
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
      <Glyph kind="ring" color={C.blue} size={12} />
      <span className={s.contextText}>
        {zoneName} · <span className="mono">{when(pass.acquired_at)}</span>
      </span>
      {t != null && Math.abs(t - moment) >= 1000 && (
        <button type="button" onClick={() => enterTime(moment)} title="Al segundo en que el radar miró la zona">
          Ir a las <span className="mono">{fmtTime.format(new Date(moment))}</span>
        </button>
      )}
      {!onIt && (
        <button type="button" onClick={() => navigate(`passes/${pass.id}`)}>
          Abrir
        </button>
      )}
    </div>
  );
}
