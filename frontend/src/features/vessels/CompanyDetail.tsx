// A company behind vessels: sanctions, and its known fleet with those Uatu has heard first.
import { ArrowLeft, ExternalLink } from "lucide-react";
import { useCompany } from "../../api/queries";
import type { FleetVessel } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C, ROLE, regionName, SOURCE } from "../../lib/constants";
import { ago } from "../../lib/format";
import { listing } from "../../lib/listing";
import { Callout, ErrorNote, Loading, Note, Section, Shape, Tag } from "../../ui";
import p from "../panel.module.css";

function FleetRow({ v }: { v: FleetVessel }) {
  const renamed = v.ais_name && v.ais_name.toUpperCase() !== v.name.toUpperCase();
  const l = listing(v);
  const facts = [
    `IMO ${v.imo}`,
    renamed && `en las listas como ${v.name}`,
    v.roles.map((r) => ROLE[r] ?? r).join(", "),
    v.sources.map((s) => SOURCE[s] ?? s).join(", "),
  ]
    .filter(Boolean)
    .join(" · ");
  const top = (
    <div className={p.rowTop}>
      <span className={p.rowTitle}>{v.ais_name ?? v.name}</span>
      {l.label && (
        <Tag shape="arrow" color={l.color}>
          {l.label}
        </Tag>
      )}
      {v.mmsi != null && <span className={p.rowTime}>{v.last_seen_at ? `oído ${ago(v.last_seen_at)}` : "oído"}</span>}
    </div>
  );
  if (v.mmsi != null) {
    return (
      <li>
        <button type="button" className={p.row} onClick={() => navigate(`vessels/${v.mmsi}`)}>
          {top}
          <div className={p.rowMeta}>{facts}</div>
        </button>
      </li>
    );
  }
  return (
    <li>
      <div className={p.row}>
        {top}
        <div className={p.rowMeta}>
          {facts} ·{" "}
          <a href={`https://www.vesselfinder.com/vessels/details/${v.imo}`} target="_blank" rel="noopener noreferrer">
            VesselFinder <ExternalLink size={10} />
          </a>
        </div>
      </div>
    </li>
  );
}

export function CompanyDetail({ id }: { id: string }) {
  const company = useCompany(id);
  const last = useUi((u) => u.lastVessel);
  const c = company.data;
  const back = (
    <button type="button" className={p.back} onClick={() => navigate(last ? `vessels/${last.mmsi}` : "vessels")}>
      <ArrowLeft size={14} /> {last ? last.name : "Barcos"}
    </button>
  );
  if (company.isPending)
    return (
      <>
        {back}
        <Loading>Cargando empresa…</Loading>
      </>
    );
  if (company.isError) return <ErrorNote error={company.error} />;
  if (!c) return null;
  const heard = c.fleet.filter((v) => v.mmsi != null).length;
  const sanctionedFleet = !c.sanctioned && c.sanctioned_vessels >= 2 && 2 * c.sanctioned_vessels >= c.vessels;
  return (
    <>
      {back}
      <div className={p.header}>
        <div className={p.eyebrow}>{c.kind === "person" ? "Persona" : "Empresa"}</div>
        <h1 className={p.title}>{c.name}</h1>
        <div className={p.subtitle}>
          {[c.countries.map(regionName).join(", "), c.imos.length > 0 && `IMO de empresa ${c.imos.join(", ")}`]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
      {(c.sanctioned || sanctionedFleet) && (
        <div className={p.callouts}>
          {c.sanctioned && (
            <Callout tone={C.critical} icon={<Shape kind="square" color={C.critical} />} title="En listas de sanciones">
              <span>{c.programs ? `Programas: ${c.programs.replaceAll(";", ", ")}. ` : ""}Fuente: OpenSanctions.</span>
            </Callout>
          )}
          {sanctionedFleet && (
            <Callout
              tone={C.serious}
              icon={<Shape kind="square" color={C.serious} />}
              title="Gestiona una flota sancionada"
            >
              <span>
                {c.sanctioned_vessels} de sus {c.vessels} barcos conocidos están en listas de sanciones.
              </span>
            </Callout>
          )}
        </div>
      )}
      <Section title={`Flota conocida · ${c.sanctioned_vessels} sancionados · ${heard} oídos`} count={c.fleet.length}>
        <ul className={p.list}>
          {c.fleet.map((v) => (
            <FleetRow key={v.imo} v={v} />
          ))}
        </ul>
      </Section>
      <Note>
        Según OpenSanctions: listas de sanciones, inteligencia ucraniana (GUR), vetos del Paris MoU y detenciones de los
        MoU de Tokio y del mar Negro de los últimos 3 años. Puede que ya no gestione alguno de estos barcos.
      </Note>
    </>
  );
}
