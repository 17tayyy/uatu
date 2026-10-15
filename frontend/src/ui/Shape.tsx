// Symbols. Shape: the map's own (circle, diamond, arrow...), small, so a legend reads like the map.
// Glyph: a list row's status mark, after Linear's status icons.
import { C } from "../lib/constants";

export type ShapeKind = "circle" | "ring" | "diamond" | "arrow" | "dash" | "line" | "square" | "hatch";

export function Shape({ kind, color, size = 12 }: { kind: ShapeKind; color: string; size?: number }) {
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
      {kind === "circle" && <circle cx={6} cy={6} r={4} fill={color} stroke={color === C.fixed ? C.muted : "none"} />}
      {kind === "ring" && <circle cx={6} cy={6} r={3.8} fill="none" stroke={color} strokeWidth={1.4} />}
      {kind === "diamond" && <path d="M6 1.2 10.8 6 6 10.8 1.2 6Z" fill={color} />}
      {kind === "arrow" && <path d="M6 1 10.2 10.8 6 8.4 1.8 10.8Z" fill={color} />}
      {kind === "dash" && <path d="M0.5 6H11.5" stroke={color} strokeWidth={1.4} strokeDasharray="2.5 1.5" />}
      {kind === "line" && <path d="M0.5 6H11.5" stroke={color} strokeWidth={1.4} />}
      {kind === "square" && <rect x={1.5} y={1.5} width={9} height={9} rx={2} fill={color} />}
      {kind === "hatch" && (
        <>
          <rect x={1} y={1} width={10} height={10} rx={2} fill="rgba(8,9,10,0.6)" stroke={C.line} />
          <path d="M1 7 7 1M1 11 11 1M5 11 11 5" stroke={color} strokeWidth={1} />
        </>
      )}
    </svg>
  );
}

export type GlyphKind = "dot" | "ring" | "dotted" | "half" | "check" | "cross" | "square" | "diamond" | "arrow";

export function Glyph({
  kind,
  color,
  size = 14,
  title,
}: {
  kind: GlyphKind;
  color: string;
  size?: number;
  title?: string;
}) {
  return (
    <svg viewBox="0 0 14 14" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
      {title ? <title>{title}</title> : null}
      {kind === "dot" && (
        <>
          <circle cx={7} cy={7} r={5.5} fill="none" stroke={color} strokeWidth={1.5} />
          <circle cx={7} cy={7} r={2.6} fill={color} />
        </>
      )}
      {kind === "ring" && <circle cx={7} cy={7} r={5.5} fill="none" stroke={color} strokeWidth={1.5} />}
      {kind === "dotted" && (
        <circle cx={7} cy={7} r={5.5} fill="none" stroke={color} strokeWidth={1.5} strokeDasharray="1.4 2.05" />
      )}
      {kind === "half" && (
        <>
          <circle cx={7} cy={7} r={5.5} fill="none" stroke={color} strokeWidth={1.5} />
          <path d="M7 3.2a3.8 3.8 0 0 1 0 7.6Z" fill={color} />
        </>
      )}
      {kind === "check" && (
        <>
          <circle cx={7} cy={7} r={6.25} fill={color} />
          <path
            d="M4.3 7.1 6.2 9 9.8 5.2"
            fill="none"
            stroke="#08090a"
            strokeWidth={1.6}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </>
      )}
      {kind === "cross" && (
        <>
          <circle cx={7} cy={7} r={6.25} fill={color} />
          <path d="M4.9 4.9 9.1 9.1M9.1 4.9 4.9 9.1" stroke="#08090a" strokeWidth={1.6} strokeLinecap="round" />
        </>
      )}
      {kind === "square" && <rect x={2} y={2} width={10} height={10} rx={2.5} fill={color} />}
      {kind === "diamond" && <path d="M7 1.4 12.6 7 7 12.6 1.4 7Z" fill={color} />}
      {kind === "arrow" && <path d="M7 1.2 11.8 12.4 7 9.6 2.2 12.4Z" fill={color} />}
    </svg>
  );
}
