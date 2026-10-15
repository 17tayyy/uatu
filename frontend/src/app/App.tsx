// The dashboard: a rail of sections, the section's panel, and the map with what floats over it.
import { Activity, ClipboardCheck, Eye, FileText, LogIn, LogOut, Satellite, Ship, UserRound } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { onUnauthorized } from "../api/client";
import { useLogout, usePending, useReports, useSession, useStatus, useZones } from "../api/queries";
import { LoginDialog } from "../features/auth/LoginDialog";
import { EventList } from "../features/events/EventList";
import { Inspector } from "../features/inspector/Inspector";
import { LayersPanel } from "../features/layers/LayersPanel";
import { PassDetail } from "../features/passes/PassDetail";
import { PassList } from "../features/passes/PassList";
import { ReviewQueue } from "../features/review/ReviewQueue";
import { StatusBar } from "../features/status/StatusBar";
import { MapContext } from "../features/time/MapContext";
import { Timebar } from "../features/time/Timebar";
import { CompanyDetail } from "../features/vessels/CompanyDetail";
import { VesselDetail } from "../features/vessels/VesselDetail";
import { VesselList } from "../features/vessels/VesselList";
import { WatchList } from "../features/watch/WatchList";
import { fmtDay } from "../lib/format";
import { MapSync } from "../map/MapSync";
import { MapView } from "../map/MapView";
import { TimeSync } from "../map/TimeSync";
import { Badge } from "../ui";
import s from "./app.module.css";
import { type Route, type Section, sectionOf, useRoute } from "./router";
import { useUi } from "./store";

const SECTIONS: { key: Section; label: string; icon: ReactNode }[] = [
  { key: "passes", label: "Pasadas", icon: <Satellite size={20} /> },
  { key: "review", label: "Revisar", icon: <ClipboardCheck size={20} /> },
  { key: "events", label: "Eventos", icon: <Activity size={20} /> },
  { key: "vessels", label: "Barcos", icon: <Ship size={20} /> },
  { key: "watch", label: "Vigilancia", icon: <Eye size={20} /> },
];

function Panel({ route }: { route: Route }) {
  switch (route.section) {
    case "passes":
      return route.passId ? <PassDetail passId={route.passId} /> : <PassList />;
    case "review":
      return <ReviewQueue />;
    case "events":
      return <EventList />;
    case "vessels":
      return route.mmsi != null ? <VesselDetail mmsi={route.mmsi} /> : <VesselList />;
    case "company":
      return <CompanyDetail id={route.companyId} />;
    case "watch":
      return <WatchList />;
  }
}

function Rail({ section }: { section: Section }) {
  const zone = useUi((u) => u.zone);
  const pending = usePending(zone).data?.features.length ?? 0;
  const alerts = useStatus(zone).data?.alerts_24h ?? 0;
  const user = useSession().data?.username;
  const reports = useReports().data;
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const logout = useLogout();
  const [menu, setMenu] = useState(false);
  return (
    <nav className={s.rail} aria-label="Secciones">
      <div className={s.logo} aria-hidden="true">
        <span />
      </div>
      {SECTIONS.map((item) => (
        <a
          key={item.key}
          href={`#/${item.key}`}
          className={s.railItem}
          aria-current={section === item.key ? "page" : undefined}
          title={item.label}
        >
          {item.icon}
          <span className={s.railLabel}>{item.label}</span>
          {item.key === "review" && pending > 0 && (
            <span className={s.railBadge}>
              <Badge>{pending}</Badge>
            </span>
          )}
          {item.key === "events" && alerts > 0 && <span className={s.railDot} title={`${alerts} alertas en 24 h`} />}
        </a>
      ))}
      <div className={s.railFoot}>
        {reports?.[0] && (
          <a
            className={s.railItem}
            href={reports[0].url}
            target="_blank"
            rel="noopener"
            title={`Informe hasta el ${fmtDay.format(new Date(`${reports[0].last_day}T12:00:00Z`))}`}
          >
            <FileText size={20} />
            <span className={s.railLabel}>Informe</span>
          </a>
        )}
        <div className={s.account}>
          <button
            type="button"
            className={s.railItem}
            aria-expanded={user ? menu : undefined}
            onClick={() => (user ? setMenu(!menu) : setLoginOpen(true))}
            title={user ? `Sesión de ${user}` : "Entrar para revisar y vigilar"}
          >
            {user ? <UserRound size={20} /> : <LogIn size={20} />}
            <span className={s.railLabel}>{user ?? "Entrar"}</span>
          </button>
          {user && menu && (
            <div className={s.menu}>
              <div className={s.menuWho}>
                Sesión de <strong>{user}</strong>
              </div>
              <button
                type="button"
                className={s.menuItem}
                onClick={() => {
                  setMenu(false);
                  logout.mutate();
                }}
              >
                <LogOut size={15} /> Salir
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
}

export function App() {
  const route = useRoute();
  const section = sectionOf(route);
  const zones = useZones();
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const expanded = useUi((u) => u.selection != null);

  useEffect(() => onUnauthorized(() => setLoginOpen(true)), [setLoginOpen]);

  return (
    <div className={s.app} data-inspector={expanded || undefined}>
      <Rail section={section} />
      <aside className={s.panel} aria-label="Panel">
        <StatusBar />
        <div className={s.panelBody} key={JSON.stringify(route)}>
          {zones.data ? <Panel route={route} /> : null}
        </div>
      </aside>
      <MapView>
        <MapSync />
        <TimeSync />
        <MapContext />
        <LayersPanel />
        <Inspector />
        <Timebar />
      </MapView>
      <LoginDialog />
      {!zones.data && <Splash failed={zones.isError} />}
    </div>
  );
}

function Splash({ failed }: { failed: boolean }) {
  return (
    <div className={s.splash} role="status">
      <div className={s.logo}>
        <span />
      </div>
      <div className={s.splashName}>Uatu</div>
      <div className={s.splashText}>{failed ? "No se pudo conectar con el servidor." : "Cargando…"}</div>
    </div>
  );
}
