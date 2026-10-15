// For an unpaired detection: how much AIS was heard around it, against what counts as well received.
import type { Detection, ReceptionRule } from "../../api/types";
import { decimal, metres } from "../../lib/format";
import { Properties } from "../../ui";
import s from "./inspector.module.css";

function check(value: string, minimum: string, met: boolean) {
  return (
    <span>
      <span className="mono">{value}</span> <span className={met ? s.ok : s.no}>{met ? "✓" : "✗"}</span>{" "}
      <span className={s.dim}>mín. {minimum}</span>
    </span>
  );
}

export function Reception({ detection: d, rule }: { detection: Detection; rule: ReceptionRule }) {
  if (d.ais_vessels_nearby == null || d.ais_reports_nearby == null) return null;
  const perVessel = d.ais_vessels_nearby ? d.ais_reports_nearby / d.ais_vessels_nearby : 0;
  const vessels = d.ais_vessels_nearby >= rule.min_vessels;
  const reports = perVessel >= rule.min_reports_per_vessel;
  return (
    <div className={s.section}>
      <div className={s.sectionTitle}>
        AIS alrededor · {metres(rule.radius_m)}, ±{decimal(rule.window_hours)} h
      </div>
      <Properties
        rows={[
          ["Barcos oídos", check(String(d.ais_vessels_nearby), String(rule.min_vessels), vessels)],
          ["Mensajes/barco", check(decimal(perVessel), decimal(rule.min_reports_per_vessel), reports)],
          ["Recepción", vessels && reports ? "buena: su silencio cuenta" : "insuficiente: su silencio no dice nada"],
        ]}
      />
    </div>
  );
}
