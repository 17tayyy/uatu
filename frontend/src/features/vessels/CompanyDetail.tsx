// A company behind vessels: sanctions, and its known fleet with those Uatu has heard first.
import { ExternalLink } from "lucide-react";
import { useCompany } from "../../api/queries";
import type { FleetVessel } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, ROLE, regionName } from "../../lib/constants";
import { ago } from "../../lib/format";
import { listing } from "../../lib/listing";
import { ErrorNote, Glyph, Group, Header, Loading, mono, Note, Properties } from "../../ui";
import p from "../panel.module.css";
import { ListingLabel } from "./VesselList";

function FleetRow({ v }: { v: FleetVessel }) {
  const renamed = v.ais_name && v.ais_name.toUpperCase() !== v.name.toUpperCase();
  const l = listing(v);
  const meta = [renamed && `en listas como ${v.name}`, v.roles.map((r) => ROLE[r] ?? r).join(", ")]
    .filter(Boolean)
    .join(" · ");
  const inner = (
    <>
      <Glyph kind={v.mmsi != null ? "dot" : "ring"} color={l.level ? l.color : C.muted} size={13} />
      <span className={p.main}>
        <span className={p.title}>{v.ais_name ?? v.name}</span>
        <span className={p.meta}>{meta}</span>
      </span>
      <ListingLabel label={l.label} color={l.color} />
      <span className={p.time}>
        {v.mmsi != null ? (v.last_seen_at ? ago(v.last_seen_at) : "oído") : `IMO ${v.imo}`}
      </span>
    </>
  );
  return (
    <li>
      {v.mmsi != null ? (
        <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
          {inner}
        </button>
      ) : (
        <a
          className={p.row}
          href={`https://www.vesselfinder.com/vessels/details/${v.imo}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          {inner}
          <ExternalLink size={11} />
        </a>
      )}
    </li>
  );
}

export function CompanyDetail({ id }: { id: string }) {
  const company = useCompany(id);
  const last = useUi((u) => u.lastVessel);
  const c = company.data;
  const crumbs: [string, (() => void) | null][] = [
    ["Barcos", () => navigate("vessels")],
    ...(last ? ([[last.name, () => navigate(`vessels/${last.mmsi}`)]] as [string, () => void][]) : []),
    [c?.name ?? "Empresa", null],
  ];
  if (company.isPending || company.isError || !c)
    return (
      <div className={p.panel}>
        <Header crumbs={crumbs} />
        {company.isError ? <ErrorNote error={company.error} /> : <Loading />}
      </div>
    );
  const heard = c.fleet.filter((v) => v.mmsi != null).length;
  const sanctionedFleet = !c.sanctioned && c.sanctioned_vessels >= 2 && 2 * c.sanctioned_vessels >= c.vessels;
  return (
    <div className={p.panel}>
      <Header crumbs={crumbs} />
      <div className={p.scroll}>
        <div className={p.head}>
          <h1 className={p.headTitle}>{c.name}</h1>
          <div className={p.headMeta}>
            <span>{c.kind === "person" ? "Persona" : "Empresa"}</span>
            {c.countries.length > 0 && <span>{c.countries.map(regionName).join(", ")}</span>}
            {c.sanctioned && (
              <ListingLabel label={c.kind === "person" ? "Sancionado" : "Sancionada"} color={C.critical} />
            )}
            {sanctionedFleet && <ListingLabel label="Flota sancionada" color={C.serious} />}
          </div>
        </div>
        <div className={p.block}>
          <Properties
            rows={[
              ["Programas", c.programs ? c.programs.replaceAll(";", ", ") : null],
              ["IMO de empresa", c.imos.length ? mono(c.imos.join(", ")) : null],
              ["Flota conocida", `${c.fleet.length} barcos · ${c.sanctioned_vessels} sancionados · ${heard} oídos`],
              ["Fuente", "OpenSanctions"],
            ]}
          />
        </div>
        <Group title="Flota" count={c.fleet.length}>
          <ul className={p.list}>
            {c.fleet.map((v) => (
              <FleetRow key={v.imo} v={v} />
            ))}
          </ul>
        </Group>
        <Note>
          Listas de sanciones, inteligencia ucraniana (GUR), vetos del Paris MoU y detenciones de los MoU de Tokio y del
          mar Negro de los últimos 3 años. Puede que ya no gestione alguno.
        </Note>
      </div>
    </div>
  );
}
