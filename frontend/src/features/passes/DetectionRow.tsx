// A radar detection in a list: what it reads as, who it may be, your verdict.
import type { Detection } from "../../api/types";
import { useUi } from "../../app/store";
import { type DetectionKind, KIND, VERDICT, type VerdictKey } from "../../lib/constants";
import { fmtTime, knots, metres } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { Glyph } from "../../ui";
import p from "../panel.module.css";

export function DetectionRow({
  detection: d,
  passId,
  lngLat,
}: {
  detection: Detection;
  passId: string;
  lngLat: LngLat;
}) {
  const selected = useUi((u) => u.selection?.kind === "detection" && u.selection.id === d.id);
  const openDetection = useUi((u) => u.openDetection);
  const kind = KIND[d.kind as DetectionKind] ?? KIND.unpaired;
  const verdict = d.review ? VERDICT[d.review.verdict as VerdictKey] : null;
  const who =
    d.kind === "matched"
      ? (d.vessel_name ?? `MMSI ${d.mmsi}`)
      : d.suspected_mmsi
        ? `¿${d.suspect_name ?? d.suspected_mmsi}?`
        : kind.label;
  const meta = [
    `${Math.round(d.length_m)} m`,
    d.kind === "matched" && d.distance_m != null ? `a ${metres(d.distance_m)}` : null,
    d.kind !== "matched" && d.suspected_mmsi ? kind.label.toLowerCase() : null,
    d.wake ? `estela ${d.wake.speed_kn != null ? knots(d.wake.speed_kn) : `${Math.round(d.wake.course_deg)}°`}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li>
      <button
        type="button"
        className={p.row}
        aria-current={selected || undefined}
        onClick={() => openDetection({ id: d.id, passId, lngLat })}
      >
        <Glyph kind={kind.glyph} color={kind.color} title={kind.label} />
        <span className={p.main}>
          <span className={p.title}>{who}</span>
          <span className={p.meta}>{meta}</span>
        </span>
        {verdict && <Glyph kind={verdict.glyph} color={verdict.color} title={verdict.label} />}
        <span className={p.time}>{fmtTime.format(new Date(d.seen_at))}</span>
      </button>
    </li>
  );
}
