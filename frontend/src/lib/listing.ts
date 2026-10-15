import { C } from "./constants";

interface Listable {
  sanctioned?: boolean | null;
  risk?: string | null;
  watched?: boolean | null;
}

/** How a vessel stands: 2 sanctioned or run by a sanctioned company, 1 on other watchlists or yours. */
export function listing(p: Listable): { level: 0 | 1 | 2; label: string | null; color: string } {
  if (p.sanctioned === true) return { level: 2, label: "Sancionado", color: C.critical };
  if (p.risk === "sanctioned_owner") return { level: 2, label: "Gestor sancionado", color: C.critical };
  if (p.watched === true) return { level: 1, label: "En tu lista", color: C.serious };
  if (p.risk === "watchlisted") return { level: 1, label: "Vigilado", color: C.serious };
  return { level: 0, label: null, color: C.text2 };
}
