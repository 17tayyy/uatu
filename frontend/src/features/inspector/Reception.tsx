// For an unpaired detection: how much AIS was heard around it, against what counts as well received.
import type { Detection, ReceptionRule } from "../../api/types";
import { decimal, metres, plural } from "../../lib/format";
import s from "./inspector.module.css";

function Row({ value, minimum, met }: { value: string; minimum: string; met: boolean }) {
  return (
    <div className={s.receptionRow}>
      <span>{value}</span>
      <span className={s.need}>
        mínimo {minimum} <span className={met ? s.met : s.unmet}>{met ? "✓" : "✗"}</span>
      </span>
    </div>
  );
}

export function Reception({ detection: d, rule }: { detection: Detection; rule: ReceptionRule }) {
  if (d.ais_vessels_nearby == null || d.ais_reports_nearby == null) return null;
  const perVessel = d.ais_vessels_nearby ? d.ais_reports_nearby / d.ais_vessels_nearby : 0;
  const vessels = d.ais_vessels_nearby >= rule.min_vessels;
  const reports = perVessel >= rule.min_reports_per_vessel;
  return (
    <div className={s.box}>
      <div className={s.boxTitle}>
        AIS alrededor ({metres(rule.radius_m)}, ±{decimal(rule.window_hours)} h)
      </div>
      <div className={s.reception}>
        <Row
          value={plural(d.ais_vessels_nearby, "barco oído", "barcos oídos")}
          minimum={String(rule.min_vessels)}
          met={vessels}
        />
        <Row
          value={`${decimal(perVessel)} mensajes por barco`}
          minimum={decimal(rule.min_reports_per_vessel)}
          met={reports}
        />
        <div className={s.verdictLine}>
          {vessels && reports
            ? "Recepción buena: su silencio cuenta."
            : "Recepción insuficiente: su silencio no dice nada."}
        </div>
      </div>
    </div>
  );
}
