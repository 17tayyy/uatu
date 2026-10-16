// The sidebar: which coast, the sections with their counts, whether Uatu is listening, who you are.
import {
  Activity,
  Check,
  ChevronDown,
  ClipboardCheck,
  Eye,
  FileText,
  LogIn,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Satellite,
  Search,
  Ship,
} from "lucide-react";
import type { ReactNode } from "react";
import {
  useLive,
  useLogout,
  usePending,
  useReports,
  useSession,
  useStatus,
  useWatches,
  useZones,
} from "../api/queries";
import { ALL } from "../lib/constants";
import { ago, fmtDay, number } from "../lib/format";
import { Kbd, menu, Popover } from "../ui";
import type { Section } from "./router";
import s from "./sidebar.module.css";
import { useUi } from "./store";

function NavItem({
  section,
  current,
  icon,
  label,
  count,
  alert,
}: {
  section: Section;
  current: Section;
  icon: ReactNode;
  label: string;
  count?: number | null;
  alert?: boolean;
}) {
  const setListHidden = useUi((u) => u.setListHidden);
  return (
    <a
      href={`#/${section}`}
      className={s.item}
      aria-current={current === section ? "page" : undefined}
      title={label}
      onClick={() => setListHidden(false)}
    >
      {icon}
      <span className={s.label}>{label}</span>
      {alert && <span className={s.alertDot} />}
      {count != null && count > 0 && <span className={s.count}>{number(count)}</span>}
    </a>
  );
}

function ZoneSwitcher() {
  const zone = useUi((u) => u.zone);
  const setZone = useUi((u) => u.setZone);
  const zones = useZones().data?.zones ?? [];
  const name = zone === ALL ? "Toda la costa" : (zones.find((z) => z.key === zone)?.name ?? zone);
  return (
    <Popover
      trigger={({ toggle, open }) => (
        <button type="button" className={s.switcher} onClick={toggle} aria-expanded={open} title="Zona">
          <span className={s.logo} aria-hidden="true">
            <span />
          </span>
          <span className={s.switcherText}>
            <span className={s.brand}>Uatu</span>
            <span className={s.zone}>{name}</span>
          </span>
          <ChevronDown size={13} className={s.switcherChevron} />
        </button>
      )}
    >
      {(close) => (
        <>
          <div className={menu.label}>Zona</div>
          {[{ key: ALL, name: "Toda la costa" }, ...zones].map((z) => (
            <button
              type="button"
              key={z.key}
              className={menu.item}
              onClick={() => {
                setZone(z.key);
                close();
              }}
            >
              {z.name}
              {z.key === zone && <Check size={14} className={menu.check} />}
            </button>
          ))}
        </>
      )}
    </Popover>
  );
}

function Account() {
  const user = useSession().data?.username;
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const logout = useLogout();
  if (!user) {
    return (
      <button type="button" className={s.item} onClick={() => setLoginOpen(true)} title="Entrar">
        <LogIn size={15} />
        <span className={s.label}>Entrar</span>
      </button>
    );
  }
  return (
    <Popover
      side="top"
      trigger={({ toggle, open }) => (
        <button type="button" className={s.item} onClick={toggle} aria-expanded={open} title={user}>
          <span className={s.avatar}>{user.charAt(0).toUpperCase()}</span>
          <span className={s.label}>{user}</span>
        </button>
      )}
    >
      {(close) => (
        <button
          type="button"
          className={menu.item}
          onClick={() => {
            close();
            logout.mutate();
          }}
        >
          <LogOut size={14} /> Cerrar sesión
        </button>
      )}
    </Popover>
  );
}

export function Sidebar({ section }: { section: Section }) {
  const zone = useUi((u) => u.zone);
  const setPaletteOpen = useUi((u) => u.setPaletteOpen);
  const status = useStatus(zone).data;
  const pending = usePending(zone).data?.features.length;
  const live = useLive(zone).data?.features.length;
  const watches = useWatches().data?.length;
  const reports = useReports().data;
  const fresh = status?.last_ais_at != null && Date.now() - new Date(status.last_ais_at).getTime() < 10 * 60_000;
  const listening = Boolean(status?.recording && fresh);
  const collapsed = useUi((u) => u.sidebarCollapsed);
  const toggleSidebar = useUi((u) => u.toggleSidebar);
  return (
    <nav className={s.sidebar} data-collapsed={collapsed || undefined} aria-label="Secciones">
      <ZoneSwitcher />
      <button type="button" className={s.search} onClick={() => setPaletteOpen(true)} title="Buscar (⌘K)">
        <Search size={14} />
        <span className={s.label}>Buscar…</span>
        <span className={s.searchKeys}>
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>
      <div className={s.items}>
        <NavItem section="passes" current={section} icon={<Satellite size={15} />} label="Pasadas" />
        <NavItem
          section="review"
          current={section}
          icon={<ClipboardCheck size={15} />}
          label="Revisar"
          count={pending}
        />
        <NavItem
          section="events"
          current={section}
          icon={<Activity size={15} />}
          label="Eventos"
          count={status?.events_24h}
          alert={(status?.alerts_24h ?? 0) > 0}
        />
        <NavItem section="vessels" current={section} icon={<Ship size={15} />} label="Barcos" count={live} />
        <NavItem section="watch" current={section} icon={<Eye size={15} />} label="Vigilancia" count={watches} />
      </div>
      <div className={s.foot}>
        <div
          className={s.status}
          title={status?.last_ais_at ? `Último mensaje AIS ${ago(status.last_ais_at)}` : undefined}
        >
          <span className={s.statusDot} data-live={listening || undefined} />
          <span className={s.label}>
            {status ? (listening ? "Grabando AIS" : "AIS detenido") : "…"}
            {status?.last_ais_at && <span className={s.statusAgo}> · {ago(status.last_ais_at)}</span>}
          </span>
        </div>
        {reports?.[0] && (
          <a className={s.item} href={reports[0].url} target="_blank" rel="noopener" title="Último informe">
            <FileText size={15} />
            <span className={s.label}>Informe</span>
            <span className={s.count}>{fmtDay.format(new Date(`${reports[0].last_day}T12:00:00Z`))}</span>
          </a>
        )}
        <Account />
        <button
          type="button"
          className={`${s.item} ${s.fold}`}
          onClick={toggleSidebar}
          title={collapsed ? "Desplegar la barra ([)" : "Plegar la barra ([)"}
          aria-label={collapsed ? "Desplegar la barra" : "Plegar la barra"}
        >
          {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          <span className={s.label}>Plegar</span>
          <span className={s.count}>[</span>
        </button>
      </div>
    </nav>
  );
}
