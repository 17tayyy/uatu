// The layers, grouped, each with what its symbols mean right under it: the legend is where the switch is.
import { Layers, X } from "lucide-react";
import { type ReactNode, useEffect } from "react";
import { usePass } from "../../api/queries";
import { useUi } from "../../app/store";
import { C, type DetectionKind, KIND, RECEPTION, SEVERITY } from "../../lib/constants";
import { LAYER_GROUPS, LAYERS, type LayerKey } from "../../map/layerConfig";
import { Shape, type ShapeKind } from "../../ui/Shape";
import s from "./layers.module.css";

function Item({ shape, color, children }: { shape: ShapeKind; color: string; children: ReactNode }) {
  return (
    <li className={s.item}>
      <Shape kind={shape} color={color} size={11} />
      {children}
    </li>
  );
}

function Legend({ layer }: { layer: LayerKey }) {
  const passId = useUi((u) => u.passId);
  const minSeverity = useUi((u) => u.eventFilter.minSeverity);
  const pass = usePass(passId).data;
  switch (layer) {
    case "detections": {
      const present = new Set(pass?.detections_geojson.features.map((f) => f.properties.kind));
      const kinds = (Object.keys(KIND) as DetectionKind[]).filter((k) => present.has(k));
      return kinds.length ? (
        <ul className={s.legend}>
          {kinds.map((k) => (
            <Item key={k} shape="circle" color={KIND[k].color}>
              {KIND[k].label}
            </Item>
          ))}
        </ul>
      ) : null;
    }
    case "passAis":
      return (
        <ul className={s.legend}>
          <Item shape="ring" color={C.text2}>
            Posición AIS al pasar el satélite
          </Item>
        </ul>
      );
    case "unimaged":
      return (
        <ul className={s.legend}>
          <Item shape="hatch" color={C.text2}>
            {pass ? "Fuera de esta pasada: sin analizar" : "Sin pasada: sin analizar"}
          </Item>
        </ul>
      );
    case "vessels":
      return (
        <ul className={s.legend}>
          <Item shape="arrow" color={C.text2}>
            Navegando (rumbo)
          </Item>
          <Item shape="circle" color={C.text2}>
            Parado
          </Item>
          <Item shape="arrow" color={C.critical}>
            Sancionado o con gestor sancionado
          </Item>
          <Item shape="arrow" color={C.serious}>
            En listas de vigilancia o en la tuya
          </Item>
        </ul>
      );
    case "events":
      return (
        <ul className={s.legend}>
          {[3, 2, 1]
            .filter((sev) => sev >= minSeverity)
            .map((sev) => (
              <Item key={sev} shape="diamond" color={SEVERITY[sev]!.color}>
                {SEVERITY[sev]!.label}
              </Item>
            ))}
        </ul>
      );
    case "density":
      return (
        <ul className={s.legend}>
          <Item shape="square" color={C.coverage.poor}>
            Poco tráfico
          </Item>
          <Item shape="square" color={C.coverage.good}>
            Mucho tráfico
          </Item>
        </ul>
      );
    case "coverage":
      return (
        <ul className={s.legend}>
          {Object.values(RECEPTION).map((r) => (
            <Item key={r.label} shape="square" color={r.color}>
              {r.label}
            </Item>
          ))}
        </ul>
      );
    case "infrastructure":
      return (
        <ul className={s.legend}>
          <Item shape="dash" color={C.infra}>
            Cable submarino
          </Item>
          <Item shape="line" color={C.infra}>
            Gasoducto u oleoducto
          </Item>
        </ul>
      );
    default:
      return null;
  }
}

export function LayersPanel() {
  const open = useUi((u) => u.layersOpen);
  const setOpen = useUi((u) => u.setLayersOpen);
  const layers = useUi((u) => u.layers);
  const toggle = useUi((u) => u.toggleLayer);
  const inspector = useUi((u) => u.selection != null);
  // Something opened in the inspector: the map needs the room more than the layers.
  useEffect(() => {
    if (inspector) setOpen(false);
  }, [inspector, setOpen]);
  if (!open) {
    return (
      <button type="button" className={s.toggle} data-shifted={inspector || undefined} onClick={() => setOpen(true)}>
        <Layers size={16} /> Capas
      </button>
    );
  }
  return (
    <div className={s.panel} data-shifted={inspector || undefined}>
      <div className={s.head}>
        <Layers size={15} /> Capas
        <button type="button" className={s.close} onClick={() => setOpen(false)} aria-label="Cerrar capas">
          <X size={15} />
        </button>
      </div>
      <div className={s.body}>
        {LAYER_GROUPS.map((group) => (
          <section key={group.title} className={s.group}>
            <h3 className={s.groupTitle}>{group.title}</h3>
            {group.layers.map((key) => (
              <div key={key}>
                <label className={s.switch}>
                  <input type="checkbox" checked={layers[key]} onChange={() => toggle(key)} />
                  <span className={s.track} aria-hidden="true" />
                  <span className={s.label}>{LAYERS[key].label}</span>
                  {LAYERS[key].hint && <span className={s.hint}>{LAYERS[key].hint}</span>}
                </label>
                {layers[key] && <Legend layer={key} />}
              </div>
            ))}
          </section>
        ))}
        <ul className={s.legend} style={{ marginTop: 4 }}>
          <Item shape="dash" color={C.muted}>
            Límites de las zonas vigiladas
          </Item>
        </ul>
      </div>
    </div>
  );
}
