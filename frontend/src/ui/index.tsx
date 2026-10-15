// Small building blocks shared by every panel.
import { ChevronRight } from "lucide-react";
import { type ButtonHTMLAttributes, type ReactNode, useId, useState } from "react";
import { Shape, type ShapeKind } from "./Shape";
import s from "./ui.module.css";

export { Shape } from "./Shape";

/** A status marker: the shape in its colour beside a label. */
export function Tag({ shape = "circle", color, children }: { shape?: ShapeKind; color: string; children: ReactNode }) {
  return (
    <span className={s.tag}>
      <Shape kind={shape} color={color} />
      {children}
    </span>
  );
}

export function Pill({ children, color }: { children: ReactNode; color?: string }) {
  return (
    <span className={s.pill}>
      {color && <Shape kind="circle" color={color} size={9} />}
      {children}
    </span>
  );
}

export function Badge({ children }: { children: ReactNode }) {
  return <span className={s.badge}>{children}</span>;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost";
  size?: "default" | "small";
};

export function Button({ variant = "default", size = "default", className, ...props }: ButtonProps) {
  const classes = [s.button, variant !== "default" && s[variant], size === "small" && s.small, className];
  return <button type="button" {...props} className={classes.filter(Boolean).join(" ")} />;
}

export function LinkButton(props: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className={[s.linkButton, props.className].filter(Boolean).join(" ")} />;
}

/** A titled part of a panel; collapsible when given `collapsed` (its initial state). */
export function Section({
  title,
  count,
  collapsed,
  action,
  children,
}: {
  title: string;
  count?: number;
  collapsed?: boolean;
  action?: ReactNode;
  children: ReactNode;
}) {
  const [closed, setClosed] = useState(collapsed ?? false);
  const id = useId();
  const head = (
    <>
      {title}
      {count != null && <span className={s.sectionCount}>{count}</span>}
    </>
  );
  if (collapsed == null) {
    return (
      <section className={s.section}>
        <div className={s.sectionHead}>
          {head}
          {action && <span style={{ marginLeft: "auto" }}>{action}</span>}
        </div>
        {children}
      </section>
    );
  }
  return (
    <section className={`${s.section} ${closed ? "" : s.open}`}>
      <button
        type="button"
        className={s.sectionHead}
        aria-expanded={!closed}
        aria-controls={id}
        onClick={() => setClosed(!closed)}
      >
        {head}
        <ChevronRight size={14} className={s.chevron} />
      </button>
      <div id={id} hidden={closed}>
        {children}
      </div>
    </section>
  );
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v != null && v !== "" && v !== false);
  return (
    <dl className={s.kv}>
      {shown.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Callout({
  tone,
  title,
  icon,
  children,
}: {
  tone: string;
  title: ReactNode;
  icon?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={s.callout} style={{ "--tone": tone } as React.CSSProperties}>
      <div className={s.calloutTitle}>
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className={s.note}>{children}</p>;
}

export function Loading({ children = "Cargando…" }: { children?: ReactNode }) {
  return (
    <div className={s.state} role="status">
      <span className={s.spinner} />
      {children}
    </div>
  );
}

export function Spinner() {
  return <span className={`${s.spinner} ${s.spinnerSmall}`} />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className={s.state}>{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <div className={s.state}>No se pudo cargar: {error instanceof Error ? error.message : String(error)}</div>;
}

export function Segmented<T extends string | number>({
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
    <fieldset className={s.segmented} aria-label={label}>
      {options.map(([v, text]) => (
        <button
          type="button"
          key={String(v)}
          className={s.segment}
          aria-pressed={v === value}
          onClick={() => onChange(v)}
        >
          {text}
        </button>
      ))}
    </fieldset>
  );
}

export const fieldClass = s.field;
export const labelClass = s.label;
export const formErrorClass = s.formError;
