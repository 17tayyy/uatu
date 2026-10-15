// ⌘K: go anywhere, find any vessel, switch a layer, from the keyboard.
import {
  Activity,
  ArrowRight,
  ClipboardCheck,
  Eye,
  History,
  Layers,
  LogIn,
  LogOut,
  MapPin,
  Satellite,
  Search,
  Ship,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useLogout, usePasses, useSession, useVesselSearch, useZones } from "../../api/queries";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { ALL } from "../../lib/constants";
import { when } from "../../lib/format";
import { LAYERS, type LayerKey } from "../../map/layerConfig";
import { HOUR } from "../../map/time";
import { Kbd } from "../../ui";
import s from "./command.module.css";

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

const fold = (text: string) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

export function CommandPalette() {
  const open = useUi((u) => u.paletteOpen);
  const setOpen = useUi((u) => u.setPaletteOpen);
  const zone = useUi((u) => u.zone);
  const layers = useUi((u) => u.layers);
  const time = useUi((u) => u.time != null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const q = query.trim();
  const vessels = useVesselSearch(q);
  const passes = usePasses(zone).data ?? [];
  const zones = useZones().data?.zones ?? [];
  const user = useSession().data?.username;
  const logout = useLogout();

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      setQuery("");
      setActive(0);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const ui = useUi.getState();
    const go = (path: string) => () => navigate(path);
    const fixed: Command[] = [
      { id: "s-passes", group: "Ir a", label: "Pasadas", icon: <Satellite size={15} />, run: go("passes") },
      { id: "s-review", group: "Ir a", label: "Revisar", icon: <ClipboardCheck size={15} />, run: go("review") },
      { id: "s-events", group: "Ir a", label: "Eventos", icon: <Activity size={15} />, run: go("events") },
      { id: "s-vessels", group: "Ir a", label: "Barcos", icon: <Ship size={15} />, run: go("vessels") },
      { id: "s-watch", group: "Ir a", label: "Vigilancia", icon: <Eye size={15} />, run: go("watch") },
      time
        ? { id: "time", group: "Acciones", label: "Volver al presente", icon: <History size={15} />, run: ui.exitTime }
        : {
            id: "time",
            group: "Acciones",
            label: "Máquina del tiempo",
            icon: <History size={15} />,
            run: () => ui.enterTime(Date.now() - HOUR),
          },
      ...(Object.keys(LAYERS) as LayerKey[]).map((key) => ({
        id: `layer-${key}`,
        group: "Capas",
        label: `${layers[key] ? "Ocultar" : "Mostrar"} ${LAYERS[key].label.toLowerCase()}`,
        icon: <Layers size={15} />,
        run: () => ui.toggleLayer(key),
      })),
      ...[{ key: ALL, name: "Toda la costa" }, ...zones]
        .filter((z) => z.key !== zone)
        .map((z) => ({
          id: `zone-${z.key}`,
          group: "Zona",
          label: `Ver ${z.name}`,
          icon: <MapPin size={15} />,
          run: () => ui.setZone(z.key),
        })),
      user
        ? {
            id: "logout",
            group: "Cuenta",
            label: "Cerrar sesión",
            hint: user,
            icon: <LogOut size={15} />,
            run: () => logout.mutate(),
          }
        : {
            id: "login",
            group: "Cuenta",
            label: "Entrar",
            icon: <LogIn size={15} />,
            run: () => ui.setLoginOpen(true),
          },
    ];
    const recent = passes.slice(0, 12).map((p) => ({
      id: `pass-${p.id}`,
      group: "Pasadas",
      label: `Pasada de ${when(p.acquired_at)}`,
      hint: zones.find((z) => z.key === p.zone)?.name,
      icon: <Satellite size={15} />,
      run: go(`passes/${p.id}`),
    }));
    const needle = fold(q);
    const matching = [...fixed, ...recent].filter(
      (c) => !needle || fold(`${c.label} ${c.hint ?? ""}`).includes(needle),
    );
    const found =
      q.length >= 2
        ? (vessels.data ?? []).slice(0, 8).map((v) => ({
            id: `vessel-${v.mmsi}`,
            group: "Barcos",
            label: v.name ?? `MMSI ${v.mmsi}`,
            hint: [v.flag, v.ship_type, String(v.mmsi)].filter(Boolean).join(" · "),
            icon: <Ship size={15} />,
            run: go(`vessels/${v.mmsi}`),
          }))
        : [];
    return [...found, ...matching].slice(0, 40);
  }, [q, vessels.data, passes, zones, layers, time, zone, user, logout]);

  const run = (c: Command | undefined) => {
    if (!c) return;
    setOpen(false);
    c.run();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, commands.length - 1));
    else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
    else if (e.key === "Enter") run(commands[active]);
    else return;
    e.preventDefault();
  };

  useEffect(() => {
    dialog.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  let lastGroup = "";
  return (
    <dialog ref={dialog} className={s.palette} aria-label="Buscar y actuar" onClose={() => setOpen(false)}>
      <div className={s.inputRow}>
        <Search size={16} />
        <input
          className={s.input}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKey}
          placeholder="Buscar un barco, ir a una pasada, mostrar una capa…"
          aria-label="Buscar"
          autoFocus
        />
      </div>
      <div className={s.results} role="listbox">
        {commands.length === 0 && <div className={s.empty}>{vessels.isFetching ? "Buscando…" : "Nada coincide."}</div>}
        {commands.map((c, i) => {
          const head = c.group !== lastGroup ? c.group : null;
          lastGroup = c.group;
          return (
            <div key={c.id}>
              {head && <div className={s.group}>{head}</div>}
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                data-index={i}
                className={s.item}
                onMouseMove={() => setActive(i)}
                onClick={() => run(c)}
              >
                {c.icon}
                <span className={s.label}>{c.label}</span>
                {c.hint && <span className={s.hint}>{c.hint}</span>}
                {i === active && <ArrowRight size={14} className={s.enter} />}
              </button>
            </div>
          );
        })}
      </div>
      <div className={s.foot}>
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd> moverse <Kbd>↵</Kbd> abrir <Kbd>esc</Kbd> cerrar
      </div>
    </dialog>
  );
}
