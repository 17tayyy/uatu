// Routes live in the URL's hash (#/passes/S1D-0097DF-estrecho): links work, back and forward too.
import { useSyncExternalStore } from "react";

export type Section = "passes" | "review" | "events" | "vessels" | "watch";

export type Route =
  | { section: "passes"; passId: string | null }
  | { section: "review" }
  | { section: "events" }
  | { section: "vessels"; mmsi: number | null }
  | { section: "company"; companyId: string }
  | { section: "watch" };

export function parse(hash: string): Route {
  const [head, id] = hash.replace(/^#\/?/, "").split("/");
  switch (head) {
    case "review":
      return { section: "review" };
    case "events":
      return { section: "events" };
    case "vessels":
      return { section: "vessels", mmsi: id && /^\d+$/.test(id) ? Number(id) : null };
    case "companies":
      return id ? { section: "company", companyId: decodeURIComponent(id) } : { section: "vessels", mmsi: null };
    case "watch":
      return { section: "watch" };
    default:
      return { section: "passes", passId: id ? decodeURIComponent(id) : null };
  }
}

const subscribe = (notify: () => void) => {
  window.addEventListener("hashchange", notify);
  return () => window.removeEventListener("hashchange", notify);
};

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  return parse(hash);
}

export function navigate(path: string): void {
  window.location.hash = `#/${path}`;
}

/** The rail's section a route belongs to (a company is under vessels). */
export function sectionOf(route: Route): Section {
  return route.section === "company" ? "vessels" : route.section;
}
