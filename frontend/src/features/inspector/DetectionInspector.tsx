// A radar detection in full: what it reads as and why, your verdict, who it may be, the AIS around it.
import { History, Ship } from "lucide-react";
import { usePass, useZones } from "../../api/queries";
import { navigate } from "../../app/router";
import { type Selection, useUi } from "../../app/store";
import { type DetectionKind, KIND } from "../../lib/constants";
import { duration, fmtTime, metres, plural, utc, when } from "../../lib/format";
import { passMoment } from "../../map/passes";
import { Button, Glyph, Loading, mono, Properties } from "../../ui";
import { useReviewKeys, VerdictBox } from "../review/VerdictBox";
import { InspectorHeader } from "./Inspector";
import s from "./inspector.module.css";
import { Optical } from "./Optical";
import { Reception } from "./Reception";

type DetectionSelection = Extract<Selection, { kind: "detection" }>;

export function DetectionInspector({ selection }: { selection: DetectionSelection }) {
  const pass = usePass(selection.passId);
  const zones = useZones().data?.zones ?? [];
  const list = useUi((u) => u.reviewList);
  const openDetection = useUi((u) => u.openDetection);
  const enterTime = useUi((u) => u.enterTime);
  const d = pass.data?.detections_geojson.features.find((f) => f.properties.id === selection.id)?.properties;
  useReviewKeys(d, selection.passId);

  const data = pass.data;
  const zoneName = data ? (zones.find((z) => z.key === data.zone)?.name ?? data.zone) : "…";
  const at = d ? list.findIndex((item) => item.id === d.id) : -1;
  const crumbs: [string, (() => void) | null][] = [
    [
      data ? `${zoneName} ${fmtTime.format(new Date(data.acquired_at))}` : "Pasada",
      () => navigate(`passes/${selection.passId}`),
    ],
    ["Detección", null],
  ];
  const position =
    at >= 0
      ? {
          at,
          of: list.length,
          prev: () => list[at - 1] && openDetection(list[at - 1]!),
          next: () => list[at + 1] && openDetection(list[at + 1]!),
        }
      : null;

  if (!data) {
    return (
      <>
        <InspectorHeader crumbs={crumbs} />
        <Loading />
      </>
    );
  }
  if (!d) {
    return (
      <>
        <InspectorHeader crumbs={crumbs} />
        <p className={s.text} style={{ padding: 16 }}>
          Esta detección ya no está en la pasada: se volvió a procesar.
        </p>
      </>
    );
  }

  const kind = KIND[d.kind as DetectionKind] ?? KIND.unpaired;
  const shift = Math.abs(d.doppler_shift_m ?? 0);
  const who =
    d.kind === "matched"
      ? (d.vessel_name ?? `MMSI ${d.mmsi}`)
      : d.suspected_mmsi
        ? `¿${d.suspect_name ?? `MMSI ${d.suspected_mmsi}`}?`
        : "Eco sin AIS";

  return (
    <>
      <InspectorHeader crumbs={crumbs} position={position} />
      <div className={s.body}>
        <div className={s.head}>
          <div className={s.kind}>
            <Glyph kind={kind.glyph} color={kind.color} size={13} />
            {kind.label}
          </div>
          <h2 className={s.title}>{who}</h2>
          <div className={`${s.meta} mono`}>
            {when(d.seen_at, true)} · {utc(d.seen_at)}
          </div>
          <p className={s.text}>
            {kind.explain}
            {d.kind === "matched" && shift >= 50 && (
              <>
                {" "}
                El radar lo ve desplazado unos {metres(shift)} por el efecto Doppler: la línea discontinua lleva a su
                posición AIS.
              </>
            )}
          </p>
        </div>
        <div className={s.section}>
          <div className={s.sectionTitle}>Veredicto</div>
          <VerdictBox detection={d} passId={selection.passId} />
        </div>
        <div className={s.section}>
          <Properties
            rows={[
              ["Eslora aprox.", mono(`${Math.round(d.length_m)} m`)],
              ["Contraste", d.contrast_db != null ? mono(`${d.contrast_db} dB`) : null],
              ["VH", d.vh_db != null ? mono(`${d.vh_db} dB`) : null],
              ["Barco AIS", d.mmsi ? (d.vessel_name ?? `MMSI ${d.mmsi}`) : null],
              ["A su AIS", d.mmsi && d.distance_m != null ? mono(metres(d.distance_m)) : null],
              ["Posible barco", d.suspected_mmsi ? (d.suspect_name ?? `MMSI ${d.suspected_mmsi}`) : null],
              [
                "Sin emitir",
                d.suspected_mmsi && d.suspect_silent_minutes != null ? duration(d.suspect_silent_minutes) : null,
              ],
              ["Visto en", d.fixed_passes_seen ? plural(d.fixed_passes_seen, "pasada", "pasadas") : null],
            ]}
          />
        </div>
        <Reception detection={d} rule={data.reception_rule} />
        <div className={s.section}>
          <div className={s.actions}>
            {d.mmsi && (
              <Button size="small" onClick={() => navigate(`vessels/${d.mmsi}`)}>
                <Ship size={13} /> Barco
              </Button>
            )}
            {d.suspected_mmsi && (
              <Button size="small" onClick={() => navigate(`vessels/${d.suspected_mmsi}`)}>
                <Ship size={13} /> Posible barco
              </Button>
            )}
            <Button size="small" onClick={() => enterTime(passMoment(data))} title="El AIS de ese instante">
              <History size={13} /> Máquina del tiempo
            </Button>
          </div>
          <Optical detectionId={d.id} lngLat={selection.lngLat} />
        </div>
      </div>
    </>
  );
}
