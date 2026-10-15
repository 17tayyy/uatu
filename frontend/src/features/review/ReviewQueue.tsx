// What is left to review across every pass: candidates and reception gaps without a verdict.
import { ClipboardCheck } from "lucide-react";
import { useEffect, useMemo } from "react";
import { usePending, useSession, useZones } from "../../api/queries";
import type { PendingFeature } from "../../api/types";
import { useUi } from "../../app/store";
import { dayShort, fmtTime } from "../../lib/format";
import type { LngLat } from "../../lib/geo";
import { Button, Empty, ErrorNote, Group, Header, Kbd, Loading } from "../../ui";
import p from "../panel.module.css";
import { DetectionRow } from "../passes/DetectionRow";

export function ReviewQueue() {
  const zone = useUi((u) => u.zone);
  const pending = usePending(zone);
  const zones = useZones().data?.zones ?? [];
  const user = useSession().data?.username;
  const setReviewList = useUi((u) => u.setReviewList);
  const openDetection = useUi((u) => u.openDetection);
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const features = pending.data?.features ?? [];

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
  const first = features[0];

  return (
    <div className={p.panel}>
      <Header
        crumbs={[["Revisar", null]]}
        actions={
          first &&
          (user ? (
            <Button
              size="small"
              variant="primary"
              onClick={() =>
                openDetection({
                  id: first.properties.id,
                  passId: first.properties.pass_id,
                  lngLat: first.geometry.coordinates as LngLat,
                })
              }
            >
              Empezar <Kbd>J</Kbd>
            </Button>
          ) : (
            <Button size="small" onClick={() => setLoginOpen(true)}>
              Entrar para revisar
            </Button>
          ))
        }
      />
      <div className={p.scroll}>
        {pending.isPending ? (
          <Loading />
        ) : pending.isError ? (
          <ErrorNote error={pending.error} />
        ) : features.length === 0 ? (
          <Empty icon={<ClipboardCheck size={22} />}>Nada por revisar.</Empty>
        ) : (
          byPass.map(([passId, list]) => {
            const head = list[0]!.properties;
            return (
              <Group
                key={passId}
                title={`${dayShort(head.acquired_at)}, ${fmtTime.format(new Date(head.acquired_at))}`}
                count={list.length}
                right={name(head.zone)}
              >
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
              </Group>
            );
          })
        )}
      </div>
    </div>
  );
}
