// A radar detection in a list: what it reads as, your verdict, and who it may be.
import type { Detection } from "../../api/types";
import { useUi } from "../../app/store";
import { type DetectionKind, KIND, VERDICT, type VerdictKey } from "../../lib/constants";
import { duration, fmtTime, metres } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { Tag } from "../../ui";
import p from "../panel.module.css";

export function DetectionRow({
  detection: d,
  passId,
  lngLat,
  extra,
}: {
  detection: Detection;
  passId: string;
  lngLat: LngLat;
  extra?: string;
}) {
  const selected = useUi((u) => u.selection?.kind === "detection" && u.selection.id === d.id);
  const openDetection = useUi((u) => u.openDetection);
  const kind = KIND[d.kind as DetectionKind] ?? KIND.unpaired;
  const verdict = d.review ? VERDICT[d.review.verdict as VerdictKey] : null;
  const who =
    d.kind === "matched"
      ? `${d.vessel_name ?? `MMSI ${d.mmsi}`} · a ${metres(d.distance_m ?? 0)}`
      : d.suspected_mmsi
        ? `Posible: ${d.suspect_name ?? `MMSI ${d.suspected_mmsi}`}, ${duration(d.suspect_silent_minutes ?? 0)} sin emitir`
        : null;
  const facts = [`${Math.round(d.length_m)} m`, d.contrast_db != null && `contraste ${d.contrast_db} dB`, extra];
  return (
    <li>
      <button
        type="button"
        className={p.row}
        aria-current={selected || undefined}
        onClick={() => {
          openDetection({ id: d.id, passId, lngLat });
        }}
      >
        <div className={p.rowTop}>
          <Tag color={kind.color}>{kind.label}</Tag>
          {verdict && (
            <Tag shape="square" color={verdict.color}>
              {verdict.label}
            </Tag>
          )}
          <span className={p.grow} />
          <span className={p.rowTime}>{fmtTime.format(new Date(d.seen_at))}</span>
        </div>
        {who && <div className={p.rowText}>{who}</div>}
        {d.review?.note && <div className={p.rowText}>«{d.review.note}»</div>}
        <div className={p.rowMeta}>{facts.filter(Boolean).join(" · ")}</div>
      </button>
    </li>
  );
}
