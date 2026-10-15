// Every Sentinel-1 pass over the zone since AIS was recorded there, latest first.

import { usePasses, useZones } from "../../api/queries";
import type { PassSummary } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { ALL, C } from "../../lib/constants";
import { number, plural, when } from "../../lib/format";
import { Empty, ErrorNote, Loading, Pill } from "../../ui";
import p from "../panel.module.css";
import s from "./passes.module.css";

function PassCard({ pass, zoneName }: { pass: PassSummary; zoneName: string | null }) {
  const onMap = useUi((u) => u.passId === pass.id);
  return (
    <li>
      <button
        type="button"
        className={p.row}
        aria-current={onMap || undefined}
        onClick={() => navigate(`passes/${pass.id}`)}
      >
        <div className={p.rowTop}>
          <span className={p.rowTitle}>{when(pass.acquired_at)}</span>
          {pass.pending > 0 && <span className={s.pending}>{number(pass.pending)} por revisar</span>}
        </div>
        <div className={p.rowMeta}>
          {[
            zoneName,
            `Sentinel-${pass.platform.slice(1)}`,
            pass.orbit_direction === "ASCENDING" ? "asc." : "desc.",
            `${Math.round(pass.coverage * 100)} % de la zona`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </div>
        <div className={p.pills}>
          <Pill>{plural(pass.detections, "detección", "detecciones")}</Pill>
          <Pill color={C.blue}>{number(pass.matched)} con AIS</Pill>
          {pass.candidates > 0 && <Pill color={C.critical}>{plural(pass.candidates, "candidato", "candidatos")}</Pill>}
        </div>
      </button>
    </li>
  );
}

export function PassList() {
  const zone = useUi((u) => u.zone);
  const passes = usePasses(zone);
  const zones = useZones().data?.zones ?? [];
  const name = (key: string) => zones.find((z) => z.key === key)?.name ?? key;
  return (
    <>
      <div className={p.header}>
        <div className={p.eyebrow}>Radar</div>
        <h1 className={p.title}>Pasadas de Sentinel-1</h1>
        <div className={p.subtitle}>
          Cada detección del radar se cruza con el AIS de ese momento: lo que no emitía queda para revisar.
        </div>
      </div>
      {passes.isPending ? (
        <Loading>Cargando pasadas…</Loading>
      ) : passes.isError ? (
        <ErrorNote error={passes.error} />
      ) : passes.data.length === 0 ? (
        <Empty>Todavía no hay pasadas procesadas desde que se graba AIS.</Empty>
      ) : (
        <ul className={p.list} style={{ paddingTop: 8 }}>
          {passes.data.map((pass) => (
            <PassCard key={pass.id} pass={pass} zoneName={zone === ALL ? name(pass.zone) : null} />
          ))}
        </ul>
      )}
    </>
  );
}
