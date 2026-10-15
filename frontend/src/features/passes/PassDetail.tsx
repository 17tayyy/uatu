// One pass: what the radar saw, what is left to review, what AIS explains.
import { History } from "lucide-react";
import { useEffect, useMemo } from "react";
import { usePass, useZones } from "../../api/queries";
import type { DetectionFeature } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, type DetectionKind, KIND, TO_REVIEW } from "../../lib/constants";
import { fmtFull, number, when } from "../../lib/format";
import { boundsOf, type LngLat } from "../../lib/geo";
import { fitBounds } from "../../map/instance";
import { passMoment } from "../../map/passes";
import { Button, ErrorNote, Glyph, Group, Header, Kbd, Loading, Note } from "../../ui";
import p from "../panel.module.css";
import { DetectionRow } from "./DetectionRow";

const rank = (f: DetectionFeature) => KIND[f.properties.kind as DetectionKind]?.rank ?? 9;

export function PassDetail({ passId }: { passId: string }) {
  const pass = usePass(passId);
  const zones = useZones().data?.zones ?? [];
  const setReviewList = useUi((u) => u.setReviewList);
  const openDetection = useUi((u) => u.openDetection);
  const enterTime = useUi((u) => u.enterTime);
  const data = pass.data;

  const groups = useMemo(() => {
    const features = data?.detections_geojson.features ?? [];
    const by = (kind: string) => features.filter((f) => f.properties.kind === kind);
    return {
      toReview: features
        .filter((f) => TO_REVIEW.includes(f.properties.kind as DetectionKind) && !f.properties.review)
        .sort((a, b) => rank(a) - rank(b)),
      reviewed: features
        .filter((f) => f.properties.review)
        .sort((a, b) => (b.properties.review?.reviewed_at ?? "").localeCompare(a.properties.review?.reviewed_at ?? "")),
      matched: by("matched").sort((a, b) => b.properties.length_m - a.properties.length_m),
      noCoverage: by("no_coverage").filter((f) => !f.properties.review),
      fixed: by("fixed"),
      undetected: data?.ais_geojson.features.filter((f) => f.properties.status === "undetected").length ?? 0,
    };
  }, [data]);

  useEffect(() => {
    setReviewList(
      groups.toReview.map((f) => ({ id: f.properties.id, passId, lngLat: f.geometry.coordinates as LngLat })),
    );
    return () => setReviewList([]);
  }, [groups.toReview, passId, setReviewList]);

  useEffect(() => {
    if (data) fitBounds(boundsOf(data.footprint.coordinates), 10);
  }, [data]);

  const crumbs: [string, (() => void) | null][] = [
    ["Pasadas", () => navigate("passes")],
    [data ? when(data.acquired_at) : "…", null],
  ];
  if (pass.isPending)
    return (
      <div className={p.panel}>
        <Header crumbs={crumbs} />
        <Loading />
      </div>
    );
  if (pass.isError || !data)
    return (
      <div className={p.panel}>
        <Header crumbs={crumbs} />
        <ErrorNote error={pass.error} />
      </div>
    );

  const zoneName = zones.find((z) => z.key === data.zone)?.name ?? data.zone;
  const first = groups.toReview[0];
  const row = (f: DetectionFeature) => (
    <DetectionRow
      key={f.properties.id}
      detection={f.properties}
      passId={passId}
      lngLat={f.geometry.coordinates as LngLat}
    />
  );
  const figure = (glyph: Parameters<typeof Glyph>[0] | null, value: number, label: string) => (
    <span className={p.figure}>
      {glyph && <Glyph {...glyph} size={12} />}
      <strong>{number(value)}</strong> {label}
    </span>
  );

  return (
    <div className={p.panel}>
      <Header
        crumbs={crumbs}
        actions={
          <Button
            icon
            variant="ghost"
            onClick={() => enterTime(passMoment(data))}
            title="Máquina del tiempo en ese instante"
          >
            <History size={15} />
          </Button>
        }
      />
      <div className={p.scroll}>
        <div className={p.head}>
          <h1 className={p.headTitle}>{zoneName}</h1>
          <div className={p.headMeta}>
            <span>{fmtFull.format(new Date(data.acquired_at))}</span>
            <span>
              Sentinel-{data.platform.slice(1)} {data.orbit_direction === "ASCENDING" ? "ascendente" : "descendente"}
            </span>
            <span>{Math.round(data.coverage * 100)} % de la zona</span>
            {!data.images.length && <span>sin imagen guardada</span>}
          </div>
          <div className={p.figures} style={{ marginTop: 6 }}>
            {figure(null, data.detections, "detecciones")}
            {figure({ kind: "ring", color: C.blue }, data.matched, "con AIS")}
            {figure({ kind: "dot", color: C.critical }, data.candidates, "candidatos")}
            {figure({ kind: "dotted", color: C.muted }, data.no_coverage, "sin AIS, mala recepción")}
            {figure({ kind: "square", color: C.fixed }, data.fixed, "fijos")}
            {figure({ kind: "ring", color: C.faint }, groups.undetected, "AIS no vistos")}
          </div>
          {first && (
            <div className={p.headActions}>
              <Button
                variant="primary"
                onClick={() =>
                  openDetection({ id: first.properties.id, passId, lngLat: first.geometry.coordinates as LngLat })
                }
              >
                Revisar {groups.toReview.length} <Kbd>J</Kbd>
              </Button>
            </div>
          )}
        </div>
        <Group title="Para revisar" count={groups.toReview.length}>
          {groups.toReview.length ? (
            <ul className={p.list}>{groups.toReview.map(row)}</ul>
          ) : (
            <Note>{groups.reviewed.length ? "Todo revisado." : "Nada que revisar en esta pasada."}</Note>
          )}
        </Group>
        {groups.reviewed.length > 0 && (
          <Group title="Revisadas" count={groups.reviewed.length}>
            <ul className={p.list}>{groups.reviewed.map(row)}</ul>
          </Group>
        )}
        <Group title="Con AIS" count={groups.matched.length} collapsed>
          <ul className={p.list}>{groups.matched.map(row)}</ul>
        </Group>
        {groups.noCoverage.length > 0 && (
          <Group title="Sin AIS · mala recepción" count={groups.noCoverage.length} collapsed>
            <ul className={p.list}>{groups.noCoverage.map(row)}</ul>
          </Group>
        )}
        {groups.fixed.length > 0 && (
          <Group title="Objetos fijos" count={groups.fixed.length} collapsed>
            <ul className={p.list}>{groups.fixed.map(row)}</ul>
          </Group>
        )}
      </div>
    </div>
  );
}
