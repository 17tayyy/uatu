// Small building blocks shared by every panel, after Linear.
import { ChevronDown } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useEffect, useId, useRef, useState } from "react";
import s from "./ui.module.css";

export { Glyph, Shape } from "./Shape";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost";
  size?: "default" | "small";
  icon?: boolean; // square, an icon only
};

export function Button({ variant = "default", size = "default", icon, className, ...props }: ButtonProps) {
  const classes = [
    s.button,
    variant !== "default" && s[variant],
    size === "small" && s.small,
    icon && s.icon,
    className,
  ];
  return <button type="button" {...props} className={classes.filter(Boolean).join(" ")} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={s.kbd}>{children}</kbd>;
}

/** A panel's top bar: where you are (breadcrumbs) and what you can do there. */
export function Header({ crumbs, actions }: { crumbs: [string, (() => void) | null][]; actions?: ReactNode }) {
  return (
    <header className={s.header}>
      <nav className={s.crumbs} aria-label="Ruta">
        {crumbs.map(([label, go], i) => {
          const last = i === crumbs.length - 1;
          return (
            <span key={label} style={{ display: "contents" }}>
              {last ? (
                <span className={s.crumbCurrent}>{label}</span>
              ) : go ? (
                <button type="button" className={s.crumb} onClick={go}>
                  {label}
                </button>
              ) : (
                <span className={s.crumb}>{label}</span>
              )}
              {!last && <span className={s.crumbSep}>/</span>}
            </span>
          );
        })}
      </nav>
      {actions && <div className={s.actions}>{actions}</div>}
    </header>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className={s.toolbar}>{children}</div>;
}

/** A list's section: a sticky bar with a count, collapsible. */
export function Group({
  title,
  icon,
  count,
  right,
  collapsed = false,
  children,
}: {
  title: string;
  icon?: ReactNode;
  count?: number;
  right?: ReactNode;
  collapsed?: boolean;
  children: ReactNode;
}) {
  const [closed, setClosed] = useState(collapsed);
  const id = useId();
  return (
    <section className={closed ? s.closed : undefined}>
      <button
        type="button"
        className={s.group}
        aria-expanded={!closed}
        aria-controls={id}
        onClick={() => setClosed(!closed)}
      >
        <ChevronDown size={13} className={s.chevron} />
        {icon}
        {title}
        {count != null && <span className={s.groupCount}>{count}</span>}
        {right && <span className={s.groupRight}>{right}</span>}
      </button>
      <div id={id} hidden={closed}>
        {children}
      </div>
    </section>
  );
}

/** Label and value, one under the other: Linear's issue properties. */
export function Properties({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v != null && v !== "" && v !== false);
  return (
    <dl className={s.properties}>
      {shown.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Data (times, MMSI, dB...) in Geist Mono. */
export function mono(text: ReactNode) {
  return <span className="mono">{text}</span>;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className={s.note}>{children}</p>;
}

export function Loading() {
  return (
    <div className={s.state} role="status" aria-label="Cargando">
      <span className={s.spinner} />
    </div>
  );
}

export function Spinner() {
  return <span className={s.spinner} />;
}

export function Empty({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className={s.state}>
      {icon}
      {children}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  return <div className={s.state}>No se pudo cargar: {error instanceof Error ? error.message : String(error)}</div>;
}

export function ToggleGroup<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: [T, string][];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className={s.toggleGroup} aria-label={label}>
      {options.map(([v, text]) => (
        <button
          type="button"
          key={String(v)}
          className={s.toggleItem}
          aria-pressed={v === value}
          onClick={() => onChange(v)}
        >
          {text}
        </button>
      ))}
    </fieldset>
  );
}

/** A menu that opens from a button and closes on a click outside or Esc. */
export function Popover({
  trigger,
  children,
  align = "start",
  side = "bottom",
}: {
  trigger: (props: { open: boolean; toggle: () => void }) => ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "start" | "end";
  side?: "bottom" | "top" | "right";
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const place: React.CSSProperties =
    side === "top"
      ? { bottom: "calc(100% + 6px)", [align === "end" ? "right" : "left"]: 0 }
      : side === "right"
        ? { left: "calc(100% + 6px)", bottom: 0 }
        : { top: "calc(100% + 6px)", [align === "end" ? "right" : "left"]: 0 };
  return (
    <div className={s.popoverWrap} ref={wrap}>
      {trigger({ open, toggle: () => setOpen(!open) })}
      {open && (
        <div className={s.popover} style={place}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

export const menu = { item: s.menuItem, check: s.menuCheck, label: s.menuLabel, sep: s.menuSep };
export const fieldClass = s.field;
export const labelClass = s.label;
export const formErrorClass = s.formError;
