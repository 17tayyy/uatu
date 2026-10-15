// What is left to review across every pass: candidates and reception gaps without a verdict.
import { ClipboardCheck, Play } from "lucide-react";
import { useEffect, useMemo } from "react";
import { usePending, useSession, useZones } from "../../api/queries";
import type { PendingFeature } from "../../api/types";
import { useUi } from "../../app/store";
import { ALL } from "../../lib/constants";
import { dayHeading, fmtTime } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { Button, Empty, ErrorNote, LinkButton, Loading } from "../../ui";
import p from "../panel.module.css";
import { DetectionRow } from "../passes/DetectionRow";
import s from "./review.module.css";

export function ReviewQueue() {
  const zone = useUi((u) => u.zone);
  const pending = usePending(zone);
  const zones = useZones().data?.zones ?? [];
  const user = useSession().data?.username;
  const setReviewList = useUi((u) => u.setReviewList);
  const openDetection = useUi((u) => u.openDetection);
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const features = pending.data?.features ?? [];

  // By pass, latest first.
  const byPass = useMemo(() => {
    const groups = new Map<string, PendingFeature[]>();
    for (const f of features) {
      const list = groups.get(f.properties.pass_id);
      if (list) list.push(f);
      else groups.set(f.properties.pass_id, [f]);
    }
    return [...groups.entries()];
  }, [features]);

  useEffect(() => {
    setReviewList(
      byPass.flatMap(([, list]) =>
        list.map((f) => ({
          id: f.properties.id,
          passId: f.properties.pass_id,
          lngLat: f.geometry.coordinates as LngLat,
        })),
      ),
    );
    return () => setReviewList([]);
  }, [byPass, setReviewList]);

  const name = (key: string) => zones.find((z) => z.key === key)?.name ?? key;
  const start = () => {
    const first = features[0];
    if (!first) return;
    const lngLat = first.geometry.coordinates as LngLat;
    openDetection({ id: first.properties.id, passId: first.properties.pass_id, lngLat });
  };

  return (
    <>
      <div className={p.header}>
        <div className={p.eyebrow}>Revisión</div>
        <h1 className={p.title}>Por revisar</h1>
        <div className={p.subtitle}>
          Detecciones sin AIS donde la recepción es buena, de los últimos 30 días. Tu veredicto las saca de aquí y
          enseña al futuro clasificador.
        </div>
      </div>
      {pending.isPending ? (
        <Loading />
      ) : pending.isError ? (
        <ErrorNote error={pending.error} />
      ) : features.length === 0 ? (
        <Empty>
          <ClipboardCheck size={28} />
          Nada pendiente: todas las detecciones sin AIS tienen veredicto.
        </Empty>
      ) : (
        <>
          <div className={s.start}>
            <Button variant="primary" onClick={start}>
              <Play size={14} /> Empezar ({features.length})
            </Button>
            {!user && <LinkButton onClick={() => setLoginOpen(true)}>Entra para dar veredictos</LinkButton>}
          </div>
          <p className={s.keys}>
            Con una abierta: <kbd>1</kbd>–<kbd>4</kbd> veredicto · <kbd>J</kbd>/<kbd>K</kbd> siguiente y anterior ·{" "}
            <kbd>Esc</kbd> cerrar
          </p>
          {byPass.map(([passId, list]) => {
            const head = list[0]!.properties;
            return (
              <section key={passId}>
                <h2 className={p.dayHeading}>
                  {dayHeading(head.acquired_at)} · pasada de {fmtTime.format(new Date(head.acquired_at))}
                  {zone === ALL ? ` · ${name(head.zone)}` : ""}
                </h2>
                <ul className={p.list}>
                  {list.map((f) => (
                    <DetectionRow
                      key={f.properties.id}
                      detection={f.properties}
                      passId={passId}
                      lngLat={f.geometry.coordinates as LngLat}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
        </>
      )}
    </>
  );
}
