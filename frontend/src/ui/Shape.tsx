// The map's symbols, small: next to a label they say what a colour means, never the colour alone.
import { C } from "../lib/constants";

export type ShapeKind = "circle" | "ring" | "diamond" | "arrow" | "dash" | "line" | "square" | "hatch";

export function Shape({ kind, color, size = 12 }: { kind: ShapeKind; color: string; size?: number }) {
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} aria-hidden="true" style={{ flex: "none" }}>
      {kind === "circle" && <circle cx={6} cy={6} r={4.5} fill={color} stroke={color === C.fixed ? C.muted : "none"} />}
      {kind === "ring" && <circle cx={6} cy={6} r={4} fill="none" stroke={color} strokeWidth={1.5} />}
      {kind === "diamond" && <path d="M6 0.8 11.2 6 6 11.2 0.8 6Z" fill={color} />}
      {kind === "arrow" && <path d="M6 0.8 10.5 11 6 8.4 1.5 11Z" fill={color} />}
      {kind === "dash" && <path d="M0.5 6H11.5" stroke={color} strokeWidth={1.5} strokeDasharray="2.5 1.5" />}
      {kind === "line" && <path d="M0.5 6H11.5" stroke={color} strokeWidth={1.5} />}
      {kind === "square" && <rect x={1} y={1} width={10} height={10} rx={2} fill={color} />}
      {kind === "hatch" && (
        <>
          <rect x={1} y={1} width={10} height={10} rx={2} fill="rgba(11,13,16,0.6)" stroke={C.line} />
          <path d="M1 7 7 1M1 11 11 1M5 11 11 5" stroke={color} strokeWidth={1} />
        </>
      )}
    </svg>
  );
}
