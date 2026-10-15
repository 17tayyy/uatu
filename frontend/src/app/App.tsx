// The dashboard: the sidebar, the section's list, and the map with the inspector beside it.
import { useEffect } from "react";
import { onUnauthorized } from "../api/client";
import { useZones } from "../api/queries";
import { LoginDialog } from "../features/auth/LoginDialog";
import { CommandPalette } from "../features/command/CommandPalette";
import { EventList } from "../features/events/EventList";
import { Inspector } from "../features/inspector/Inspector";
import { OpticalCard } from "../features/inspector/Optical";
import { MapToolbar } from "../features/layers/MapToolbar";
import { PassDetail } from "../features/passes/PassDetail";
import { PassList } from "../features/passes/PassList";
import { ReviewQueue } from "../features/review/ReviewQueue";
import { MapContext } from "../features/time/MapContext";
import { Timebar } from "../features/time/Timebar";
import { CompanyDetail } from "../features/vessels/CompanyDetail";
import { VesselDetail } from "../features/vessels/VesselDetail";
import { VesselList } from "../features/vessels/VesselList";
import { WatchList } from "../features/watch/WatchList";
import { MapSync } from "../map/MapSync";
import { MapView } from "../map/MapView";
import { TimeSync } from "../map/TimeSync";
import s from "./app.module.css";
import { type Route, sectionOf, useRoute } from "./router";
import { Sidebar } from "./Sidebar";
import { useUi } from "./store";

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

export function App() {
  const route = useRoute();
  const zones = useZones();
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const setPaletteOpen = useUi((u) => u.setPaletteOpen);
  const inspector = useUi((u) => u.selection != null);
  const time = useUi((u) => u.time != null);

  useEffect(() => onUnauthorized(() => setLoginOpen(true)), [setLoginOpen]);
  // ⌘K / Ctrl+K anywhere: the command palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(!useUi.getState().paletteOpen);
        return;
      }
      // J with nothing open starts on the list being reviewed (see useReviewKeys for the rest).
      const ui = useUi.getState();
      const typing = (e.target as HTMLElement | null)?.closest("input, textarea, select, dialog");
      if (e.key === "j" && !typing && !ui.selection && ui.reviewList[0]) ui.openDetection(ui.reviewList[0]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPaletteOpen]);

  return (
    <div className={s.app} data-inspector={inspector || undefined} data-time={time || undefined}>
      <Sidebar section={sectionOf(route)} />
      <main className={s.list} key={JSON.stringify(route)}>
        {zones.data ? <Panel route={route} /> : null}
      </main>
      <div className={s.stage}>
        <MapView>
          <MapSync />
          <TimeSync />
          <MapContext />
          <MapToolbar />
          <OpticalCard />
          <Timebar />
        </MapView>
        <Inspector />
      </div>
      <LoginDialog />
      <CommandPalette />
      {!zones.data && <Splash failed={zones.isError} />}
    </div>
  );
}

function Splash({ failed }: { failed: boolean }) {
  return (
    <div className={s.splash} role="status">
      <div className={s.splashLogo} aria-hidden="true">
        <span />
      </div>
      {failed && <div className={s.splashText}>No se pudo conectar con el servidor.</div>}
    </div>
  );
}
