// Every Sentinel-1 pass over the zone since AIS was recorded there, by day.
import { useMemo } from "react";
import { usePasses, useZones } from "../../api/queries";
import type { PassSummary } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C } from "../../lib/constants";
import { dayHeading, fmtTime, number } from "../../lib/format";
import { Empty, ErrorNote, Glyph, Group, Header, Loading } from "../../ui";
import p from "../panel.module.css";

function PassRow({ pass, zoneName }: { pass: PassSummary; zoneName: string }) {
  const onMap = useUi((u) => u.passId === pass.id);
  const meta = `S${pass.platform.slice(1)} ${pass.orbit_direction === "ASCENDING" ? "asc." : "desc."} · ${Math.round(pass.coverage * 100)} %`;
  return (
    <li>
      <button
        type="button"
        className={p.row}
        aria-current={onMap || undefined}
        onClick={() => navigate(`passes/${pass.id}`)}
      >
        {pass.pending > 0 ? (
          <Glyph kind="dot" color={C.critical} title="Con detecciones por revisar" />
        ) : (
          <Glyph kind="ring" color={C.faint} title="Nada por revisar" />
        )}
        <span className={p.time} style={{ color: "var(--text-2)" }}>
          {fmtTime.format(new Date(pass.acquired_at))}
        </span>
        <span className={p.main}>
          <span className={p.title}>{zoneName}</span>
          <span className={p.meta}>{meta}</span>
        </span>
        <span className={p.right} title={`${pass.detections} detecciones, ${pass.matched} con AIS`}>
          {pass.pending > 0 && <span className={p.badge}>{pass.pending}</span>}
          <span>
            {number(pass.matched)}/{number(pass.detections)}
          </span>
        </span>
      </button>
    </li>
  );
}

export function PassList() {
  const zone = useUi((u) => u.zone);
  const passes = usePasses(zone);
  const zones = useZones().data?.zones ?? [];
  const name = (key: string) => zones.find((z) => z.key === key)?.name ?? key;
  const days = useMemo(() => {
    const groups: [string, PassSummary[]][] = [];
    for (const pass of passes.data ?? []) {
      const day = dayHeading(pass.acquired_at);
      const last = groups.at(-1);
      if (last && last[0] === day) last[1].push(pass);
      else groups.push([day, [pass]]);
    }
    return groups;
  }, [passes.data]);
  return (
    <div className={p.panel}>
      <Header crumbs={[["Pasadas", null]]} />
      <div className={p.scroll}>
        {passes.isPending ? (
          <Loading />
        ) : passes.isError ? (
          <ErrorNote error={passes.error} />
        ) : days.length === 0 ? (
          <Empty>Ninguna pasada procesada desde que se graba AIS.</Empty>
        ) : (
          days.map(([day, list]) => (
            <Group key={day} title={day} count={list.length}>
              <ul className={p.list}>
                {list.map((pass) => (
                  <PassRow key={pass.id} pass={pass} zoneName={name(pass.zone)} />
                ))}
              </ul>
            </Group>
          ))
        )}
      </div>
    </div>
  );
}
