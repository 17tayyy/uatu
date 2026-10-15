// Over the map, top right: the layers (with their legend) and the time machine.
import { History, Layers } from "lucide-react";
import type { ReactNode } from "react";
import { usePass } from "../../api/queries";
import { useUi } from "../../app/store";
import { C, type DetectionKind, KIND, RECEPTION, SEVERITY } from "../../lib/constants";
import { LAYER_GROUPS, LAYERS, type LayerKey } from "../../map/layerConfig";
import { HOUR } from "../../map/time";
import { Popover } from "../../ui";
import { Shape, type ShapeKind } from "../../ui/Shape";
import s from "./toolbar.module.css";

function Item({ shape, color, children }: { shape: ShapeKind; color: string; children: ReactNode }) {
  return (
    <li className={s.item}>
      <Shape kind={shape} color={color} size={10} />
      {children}
    </li>
  );
}

/** What a layer's symbols mean, under its switch. */
function Legend({ layer }: { layer: LayerKey }) {
  const passId = useUi((u) => u.passId);
  const minSeverity = useUi((u) => u.eventFilter.minSeverity);
  const pass = usePass(passId).data;
  const items: ReactNode[] = [];
  switch (layer) {
    case "detections": {
      const present = new Set(pass?.detections_geojson.features.map((f) => f.properties.kind));
      for (const k of Object.keys(KIND) as DetectionKind[]) {
        if (present.has(k))
          items.push(
            <Item key={k} shape="circle" color={KIND[k].color}>
              {KIND[k].label}
            </Item>,
          );
      }
      break;
    }
    case "passAis":
      items.push(
        <Item key="a" shape="ring" color={C.text2}>
          Posición AIS al pasar el satélite
        </Item>,
      );
      break;
    case "unimaged":
      items.push(
        <Item key="h" shape="hatch" color={C.text2}>
          Fuera de la pasada: sin analizar
        </Item>,
      );
      break;
    case "vessels":
      items.push(
        <Item key="m" shape="arrow" color={C.text2}>
          Navegando
        </Item>,
        <Item key="p" shape="circle" color={C.text2}>
          Parado
        </Item>,
        <Item key="s" shape="arrow" color={C.critical}>
          Sancionado o gestor sancionado
        </Item>,
        <Item key="w" shape="arrow" color={C.serious}>
          Vigilado o en tu lista
        </Item>,
      );
      break;
    case "events":
      for (const sev of [3, 2, 1].filter((x) => x >= minSeverity)) {
        items.push(
          <Item key={sev} shape="diamond" color={SEVERITY[sev]!.color}>
            {SEVERITY[sev]!.label}
          </Item>,
        );
      }
      break;
    case "density":
      items.push(
        <Item key="l" shape="square" color={C.coverage.poor}>
          Poco tráfico
        </Item>,
        <Item key="h" shape="square" color={C.coverage.good}>
          Mucho
        </Item>,
      );
      break;
    case "coverage":
      for (const r of Object.values(RECEPTION)) {
        items.push(
          <Item key={r.label} shape="square" color={r.color}>
            {r.label}
          </Item>,
        );
      }
      break;
    case "infrastructure":
      items.push(
        <Item key="c" shape="dash" color={C.infra}>
          Cable submarino
        </Item>,
        <Item key="p" shape="line" color={C.infra}>
          Gasoducto u oleoducto
        </Item>,
      );
      break;
  }
  return items.length ? <ul className={s.legend}>{items}</ul> : null;
}

function LayersMenu() {
  const layers = useUi((u) => u.layers);
  const toggle = useUi((u) => u.toggleLayer);
  return (
    <div className={s.menu}>
      {LAYER_GROUPS.map((group) => (
        <section key={group.title} className={s.group}>
          <h3 className={s.groupTitle}>{group.title}</h3>
          {group.layers.map((key) => (
            <div key={key}>
              <label className={s.switch}>
                <span className={s.label}>{LAYERS[key].label}</span>
                {LAYERS[key].hint && <span className={s.hint}>{LAYERS[key].hint}</span>}
                <input type="checkbox" checked={layers[key]} onChange={() => toggle(key)} />
                <span className={s.track} aria-hidden="true" />
              </label>
              {layers[key] && <Legend layer={key} />}
            </div>
          ))}
        </section>
      ))}
      <ul className={s.legend} style={{ margin: "6px 0 2px" }}>
        <Item shape="dash" color={C.muted}>
          Límites de las zonas vigiladas
        </Item>
      </ul>
    </div>
  );
}

export function MapToolbar() {
  const time = useUi((u) => u.time != null);
  const enterTime = useUi((u) => u.enterTime);
  const exitTime = useUi((u) => u.exitTime);
  return (
    <div className={s.toolbar}>
      <button
        type="button"
        className={s.button}
        aria-pressed={time}
        onClick={() => (time ? exitTime() : enterTime(Date.now() - HOUR))}
        title={time ? "Volver al presente" : "Máquina del tiempo"}
      >
        <History size={14} /> Máquina del tiempo
      </button>
      <Popover
        align="end"
        trigger={({ toggle, open }) => (
          <button type="button" className={s.button} aria-expanded={open} onClick={toggle}>
            <Layers size={14} /> Capas
          </button>
        )}
      >
        {() => <LayersMenu />}
      </Popover>
    </div>
  );
}
