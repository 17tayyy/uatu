// A radar detection in full: what it reads as and why, who it may be, the AIS around it, your verdict.
import { ChevronDown, ChevronUp, Clock, Ship } from "lucide-react";
import { usePass } from "../../api/queries";
import { navigate, useRoute } from "../../app/router";
import { type Selection, useUi } from "../../app/store";
import { type DetectionKind, KIND } from "../../lib/constants";
import { duration, fmtTime, metres, plural, utc, when } from "../../lib/format";
import { passMoment } from "../../map/passes";
import { Button, KeyValues, Loading, Tag } from "../../ui";
import { useReviewKeys, VerdictBox } from "../review/VerdictBox";
import s from "./inspector.module.css";
import { Optical } from "./Optical";
import { Reception } from "./Reception";

type DetectionSelection = Extract<Selection, { kind: "detection" }>;

export function DetectionInspector({ selection }: { selection: DetectionSelection }) {
  const pass = usePass(selection.passId);
  const route = useRoute();
  const list = useUi((u) => u.reviewList);
  const openDetection = useUi((u) => u.openDetection);
  const enterTime = useUi((u) => u.enterTime);
  const d = pass.data?.detections_geojson.features.find((f) => f.properties.id === selection.id)?.properties;
  useReviewKeys(d, selection.passId);

  if (!pass.data) return <Loading />;
  if (!d) return <div className={s.body}>Esta detección ya no está en la pasada (se volvió a procesar).</div>;

  const kind = KIND[d.kind as DetectionKind] ?? KIND.unpaired;
  const at = list.findIndex((item) => item.id === d.id);
  const onPass = route.section === "passes" && route.passId === selection.passId;
  const shift = Math.abs(d.doppler_shift_m ?? 0);

  return (
    <>
      <div className={s.body}>
        <div className={s.head}>
          <Tag color={kind.color}>{kind.label}</Tag>
          <h2 className={s.title}>Detección radar</h2>
          <div className={s.when}>
            {when(d.seen_at, true)} · {utc(d.seen_at)} · pasada de {fmtTime.format(new Date(pass.data.acquired_at))}
          </div>
        </div>
        <p className={s.text}>{kind.explain}</p>
        {d.kind === "matched" && shift >= 50 && (
          <p className={s.text}>
            El radar ve desplazados a lo largo de su traza los barcos que se acercan o se alejan de él (efecto Doppler):
            a este, unos {metres(shift)}. La línea discontinua lleva a donde estaba según su AIS.
          </p>
        )}
        <KeyValues
          rows={[
            ["Eslora aprox.", `${Math.round(d.length_m)} m`],
            ["Contraste", d.contrast_db != null ? `${d.contrast_db} dB` : null],
            ["VH", d.vh_db != null ? `${d.vh_db} dB` : null],
            ["Barco AIS", d.mmsi ? (d.vessel_name ?? `MMSI ${d.mmsi}`) : null],
            ["Distancia", d.mmsi && d.distance_m != null ? metres(d.distance_m) : null],
            ["Posible barco", d.suspected_mmsi ? (d.suspect_name ?? `MMSI ${d.suspected_mmsi}`) : null],
            [
              "Sin emitir",
              d.suspected_mmsi && d.suspect_silent_minutes != null ? duration(d.suspect_silent_minutes) : null,
            ],
            ["Visto en", d.fixed_passes_seen ? plural(d.fixed_passes_seen, "pasada", "pasadas") : null],
          ]}
        />
        <Reception detection={d} rule={pass.data.reception_rule} />
        <div className={s.box}>
          <div className={s.boxTitle}>Tu veredicto</div>
          <VerdictBox detection={d} passId={selection.passId} />
        </div>
        <div className={s.actions}>
          {d.mmsi && (
            <Button size="small" onClick={() => navigate(`vessels/${d.mmsi}`)}>
              <Ship size={13} /> Ver barco
            </Button>
          )}
          {d.suspected_mmsi && (
            <Button size="small" onClick={() => navigate(`vessels/${d.suspected_mmsi}`)}>
              <Ship size={13} /> Posible barco
            </Button>
          )}
          {!onPass && (
            <Button size="small" onClick={() => navigate(`passes/${selection.passId}`)}>
              Ver pasada
            </Button>
          )}
          <Button size="small" onClick={() => enterTime(passMoment(pass.data))} title="El AIS de ese instante">
            <Clock size={13} /> Máquina del tiempo
          </Button>
        </div>
        <Optical detectionId={d.id} lngLat={selection.lngLat} />
      </div>
      {at >= 0 && (
        <div className={s.foot}>
          <span className="num">
            {at + 1} de {list.length} por revisar
          </span>
          <span className={s.grow} />
          <Button size="small" variant="ghost" disabled={at === 0} onClick={() => openDetection(list[at - 1]!)}>
            <ChevronUp size={14} /> K
          </Button>
          <Button
            size="small"
            variant="ghost"
            disabled={at === list.length - 1}
            onClick={() => openDetection(list[at + 1]!)}
          >
            <ChevronDown size={14} /> J
          </Button>
        </div>
      )}
    </>
  );
}
