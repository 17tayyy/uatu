// What things are called and how they look: one place for the map and the panels alike.
import type { GlyphKind } from "../ui/Shape";

export const API = "/api/v1";
export const ALL = "todas"; // the whole coast, as a zone key
export const REFRESH_MS = 60_000;

/** The palette. Map styles need literal colours; the CSS has the same values as custom properties. */
export const C = {
  bg: "#08090a",
  surface: "#0c0d0f",
  line: "#25272b",
  text: "#f7f8f8",
  text2: "#c9ced6",
  muted: "#8a8f98",
  faint: "#62666d",
  accent: "#5e6ad2",
  blue: "#4ea7fc",
  good: "#4cb782",
  warning: "#f2c94c",
  serious: "#f2994a",
  critical: "#eb5757",
  fixed: "#3b3e44",
  land: "#101113",
  coast: "#2c2f35",
  infra: "#6b7079",
  coverage: { good: "#9cc9fd", patchy: "#4ea7fc", poor: "#1f5ea8" },
} as const;

export type DetectionKind =
  | "went_silent"
  | "unidentified"
  | "reception_gap"
  | "matched"
  | "no_coverage"
  | "unpaired"
  | "fixed";

/** How each radar detection reads, most worth a look first. */
export const KIND: Record<
  DetectionKind,
  { label: string; color: string; rank: number; glyph: GlyphKind; explain: string }
> = {
  went_silent: {
    label: "Dejó de emitir",
    color: C.critical,
    rank: 0,
    glyph: "dot",
    explain: "Ningún AIS aquí, pero un barco que dejó de emitir hace rato pudo llegar hasta este punto.",
  },
  unidentified: {
    label: "Oscuro",
    color: C.critical,
    rank: 1,
    glyph: "dot",
    explain: "Ningún AIS aquí, ningún barco callado que lo explique y buena recepción alrededor.",
  },
  reception_gap: {
    label: "Hueco de recepción",
    color: C.warning,
    rank: 2,
    glyph: "half",
    explain: "Sin AIS aquí, pero un barco que lleva poco sin oírse pudo estar en este punto: probablemente recepción.",
  },
  matched: {
    label: "Con AIS",
    color: C.blue,
    rank: 3,
    glyph: "ring",
    explain: "Emparejado con un barco que emitía AIS.",
  },
  no_coverage: {
    label: "Sin AIS · mala recepción",
    color: C.muted,
    rank: 4,
    glyph: "dotted",
    explain: "Ningún AIS aquí, pero alrededor aisstream oye poco: puede que emitiera y no le llegara.",
  },
  unpaired: {
    label: "Antes de grabar AIS",
    color: C.muted,
    rank: 5,
    glyph: "dotted",
    explain: "Pasada anterior a la grabación de AIS.",
  },
  fixed: {
    label: "Objeto fijo",
    color: C.fixed,
    rank: 6,
    glyph: "square",
    explain: "Un punto que aparece pasada tras pasada: plataforma, boya o fondeo largo.",
  },
};
export const TO_REVIEW: DetectionKind[] = ["went_silent", "unidentified", "reception_gap"];

export type VerdictKey = "vessel_without_ais" | "vessel_with_ais" | "reception_gap" | "not_a_vessel";

/** Your verdict on a detection. The order is the keyboard's: 1 to 4. */
export const VERDICT: Record<
  VerdictKey,
  { label: string; color: string; vessel: boolean; hint: string; glyph: GlyphKind }
> = {
  vessel_without_ais: { label: "Barco sin AIS", color: C.critical, vessel: true, hint: "No emitía", glyph: "check" },
  vessel_with_ais: { label: "Barco con AIS", color: C.blue, vessel: true, hint: "Falló el emparejado", glyph: "check" },
  reception_gap: {
    label: "Hueco de recepción",
    color: C.warning,
    vessel: true,
    hint: "Emitía, no se oyó",
    glyph: "check",
  },
  not_a_vessel: { label: "No es un barco", color: C.muted, vessel: false, hint: "Ruido o estructura", glyph: "cross" },
};
export const VERDICT_KEYS = Object.keys(VERDICT) as VerdictKey[];

export const SEVERITY: Record<number, { label: string; color: string }> = {
  3: { label: "Alerta", color: C.critical },
  2: { label: "Notable", color: C.serious },
  1: { label: "Info", color: C.muted },
};

// Who owns and manages vessels (OpenSanctions): roles, sources and other watchlists.
export const ROLE: Record<string, string> = {
  owner: "Propietario",
  commercial_manager: "Gestor comercial",
  ism_manager: "Gestor ISM (seguridad)",
  linked: "Vinculada",
  insurer: "Aseguradora P&I",
};
export const SOURCE: Record<string, string> = {
  sanctions: "listas de sanciones",
  ua_war_sanctions: "GUR (Ucrania)",
  paris_mou_banned: "Paris MoU",
  tokyo_mou_detention: "detención, MoU de Tokio",
  black_sea_mou_detention: "detención, MoU del mar Negro",
};
export const WATCHLIST: Record<string, string> = {
  shadow_fleet: "Flota en la sombra rusa, según la inteligencia ucraniana (GUR)",
  war_and_sanctions: "En la lista de la inteligencia ucraniana (GUR) de barcos que trabajan para Rusia",
  port_ban: "Vetado en los puertos europeos (Paris MoU)",
};

export const RECEPTION = {
  good: { label: "Buena (≥ 3 mensajes por barco)", color: C.coverage.good },
  patchy: { label: "Irregular (1,5–3)", color: C.coverage.patchy },
  poor: { label: "Pobre (< 1,5)", color: C.coverage.poor },
};

// Where a vessel stopped.
export const STOP: Record<string, { label: string; title: string; color: string }> = {
  berth: { label: "Puerto", title: "En puerto", color: C.blue },
  anchorage: { label: "Fondeo", title: "Fondeado", color: C.text2 },
  drifting: { label: "Parado", title: "Parado mar adentro", color: C.serious },
};
export const COUNTRY: Record<string, string> = {
  Spain: "España",
  Morocco: "Marruecos",
  Algeria: "Argelia",
  France: "Francia",
  Italy: "Italia",
  Tunisia: "Túnez",
};

export const regionName = (() => {
  const names = new Intl.DisplayNames(["es"], { type: "region" });
  return (code: string) => {
    try {
      return names.of(code.toUpperCase()) ?? code.toUpperCase();
    } catch {
      return code.toUpperCase();
    }
  };
})();
