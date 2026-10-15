// One pass: what the radar saw, what is left to review, and what AIS explains.
import { ArrowLeft, Clock, Play } from "lucide-react";
import { useEffect, useMemo } from "react";
import { usePass, useZones } from "../../api/queries";
import type { DetectionFeature } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, type DetectionKind, KIND, TO_REVIEW } from "../../lib/constants";
import { fmtFull, number } from "../../lib/format";
import { boundsOf, type LngLat } from "../../lib/geo";
import { fitBounds } from "../../map/instance";
import { passMoment } from "../../map/passes";
import { Button, ErrorNote, Loading, Note, Section, Shape } from "../../ui";
import p from "../panel.module.css";
import { DetectionRow } from "./DetectionRow";
import s from "./passes.module.css";

const rank = (f: DetectionFeature) => KIND[f.properties.kind as DetectionKind]?.rank ?? 9;

function Stat({ value, label, color, ring }: { value: number; label: string; color?: string; ring?: boolean }) {
  return (
    <div className={p.stat}>
      <div className={p.statValue}>
        {color && <Shape kind={ring ? "ring" : "circle"} color={color} size={10} />}
        {number(value)}
      </div>
      <div className={p.statLabel}>{label}</div>
    </div>
  );
}

export function PassDetail({ passId }: { passId: string }) {
  const pass = usePass(passId);
  const zones = useZones().data?.zones ?? [];
  const setReviewList = useUi((u) => u.setReviewList);
  const openDetection = useUi((u) => u.openDetection);
  const enterTime = useUi((u) => u.enterTime);
  const data = pass.data;

  const groups = useMemo(() => {
    const features = data?.detections_geojson.features ?? [];
    const by = (kinds: string[]) => features.filter((f) => kinds.includes(f.properties.kind));
    return {
      toReview: features
        .filter((f) => TO_REVIEW.includes(f.properties.kind as DetectionKind) && !f.properties.review)
        .sort((a, b) => rank(a) - rank(b)),
      reviewed: features
        .filter((f) => f.properties.review)
        .sort((a, b) => (b.properties.review?.reviewed_at ?? "").localeCompare(a.properties.review?.reviewed_at ?? "")),
      matched: by(["matched"]).sort((a, b) => b.properties.length_m - a.properties.length_m),
      noCoverage: by(["no_coverage"]).filter((f) => !f.properties.review),
      fixed: by(["fixed"]),
      undetected: data?.ais_geojson.features.filter((f) => f.properties.status === "undetected").length ?? 0,
    };
  }, [data]);

  // The keyboard reviews this pass's list, in order.
  useEffect(() => {
    setReviewList(
      groups.toReview.map((f) => ({ id: f.properties.id, passId, lngLat: f.geometry.coordinates as LngLat })),
    );
    return () => setReviewList([]);
  }, [groups.toReview, passId, setReviewList]);

  useEffect(() => {
    if (data) fitBounds(boundsOf(data.footprint.coordinates), 10);
  }, [data]);

  if (pass.isPending) return <Loading>Cargando pasada…</Loading>;
  if (pass.isError) return <ErrorNote error={pass.error} />;
  if (!data) return null;

  const zoneName = zones.find((z) => z.key === data.zone)?.name ?? data.zone;
  const row = (f: DetectionFeature) => (
    <DetectionRow
      key={f.properties.id}
      detection={f.properties}
      passId={passId}
      lngLat={f.geometry.coordinates as LngLat}
    />
  );
  const first = groups.toReview[0];

  return (
    <>
      <button type="button" className={p.back} onClick={() => navigate("passes")}>
        <ArrowLeft size={14} /> Pasadas
      </button>
      <div className={p.header}>
        <div className={p.eyebrow}>
          {zoneName} · Sentinel-{data.platform.slice(1)} ·{" "}
          {data.orbit_direction === "ASCENDING" ? "ascendente" : "descendente"}
        </div>
        <h1 className={p.title}>{fmtFull.format(new Date(data.acquired_at))}</h1>
        <div className={p.subtitle}>
          {Math.round(data.coverage * 100)} % de la zona{data.frames > 1 ? ` · ${data.frames} tomas` : ""}
          {!data.images.length && " · sin imagen radar guardada"}
        </div>
      </div>
      <div className={p.stats}>
        <Stat value={data.detections} label="detecciones" />
        <Stat value={data.matched} label="con AIS" color={C.blue} />
        <Stat value={data.candidates} label="candidatos" color={C.critical} />
        <Stat value={data.no_coverage} label="sin AIS, mala recepción" color={C.muted} />
        <Stat value={data.fixed} label="objetos fijos" color={C.fixed} />
        <Stat value={groups.undetected} label="AIS no vistos" color={C.text2} ring />
      </div>
      <div className={s.startReview}>
        {first && (
          <Button
            variant="primary"
            onClick={() => {
              const lngLat = first.geometry.coordinates as LngLat;
              openDetection({ id: first.properties.id, passId, lngLat });
            }}
          >
            <Play size={14} /> Revisar ({groups.toReview.length})
          </Button>
        )}
        <Button onClick={() => enterTime(passMoment(data))} title="Ver el AIS de ese momento, al segundo">
          <Clock size={14} /> Máquina del tiempo
        </Button>
      </div>

      <Section title="Para revisar" count={groups.toReview.length}>
        {groups.toReview.length ? (
          <ul className={p.list}>{groups.toReview.map(row)}</ul>
        ) : (
          <Note>
            {groups.reviewed.length
              ? "Todo revisado."
              : "Nada que revisar: cada detección tiene AIS, es un objeto fijo o está donde no se recibe AIS."}
          </Note>
        )}
      </Section>
      {groups.reviewed.length > 0 && (
        <Section title="Revisadas" count={groups.reviewed.length}>
          <ul className={p.list}>{groups.reviewed.map(row)}</ul>
        </Section>
      )}
      <Section title="Con AIS" count={groups.matched.length} collapsed>
        <ul className={p.list}>{groups.matched.map(row)}</ul>
      </Section>
      {groups.noCoverage.length > 0 && (
        <Section title="Sin AIS · mala recepción" count={groups.noCoverage.length} collapsed>
          <Note>Donde aisstream oye poco, que no haya AIS no dice nada: puede que emitieran y no les llegara.</Note>
          <ul className={p.list}>{groups.noCoverage.map(row)}</ul>
        </Section>
      )}
      {groups.fixed.length > 0 && (
        <Section title="Objetos fijos" count={groups.fixed.length} collapsed>
          <ul className={p.list}>{groups.fixed.map(row)}</ul>
        </Section>
      )}
    </>
  );
}
