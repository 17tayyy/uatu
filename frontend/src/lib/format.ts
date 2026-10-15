// Dates in the peninsula's time, numbers the Spanish way.

export const TZ = "Europe/Madrid";

const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ });
export const fmtTime = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const fmtSeconds = new Intl.DateTimeFormat("es-ES", {
  timeZone: TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
export const fmtDay = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, day: "numeric", month: "short" });
const fmtWeekday = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });
export const fmtFull = new Intl.DateTimeFormat("es-ES", { timeZone: TZ, dateStyle: "long", timeStyle: "short" });
const fmtNumber = new Intl.NumberFormat("es-ES");
const fmtMonth = new Intl.DateTimeFormat("es-ES", { timeZone: "UTC", month: "short", year: "numeric" });

export const number = (n: number) => fmtNumber.format(n);
export const month = (isoDate: string) => fmtMonth.format(new Date(isoDate));
export const capitalise = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

export function sameDay(a: Date, b: Date): boolean {
  return dayKey.format(a) === dayKey.format(b);
}

/** "hoy 08:18", "ayer 20:17", "12 oct 06:26"; to the second when asked. */
export function when(iso: string | number | Date, seconds = false): string {
  const d = new Date(iso);
  const day = dayKey.format(d);
  const prefix =
    day === dayKey.format(new Date())
      ? "hoy"
      : day === dayKey.format(new Date(Date.now() - 86_400_000))
        ? "ayer"
        : fmtDay.format(d);
  return `${prefix} ${(seconds ? fmtSeconds : fmtTime).format(d)}`;
}

/** "Hoy", "Ayer", "Lunes 12 de octubre": the heading of a day's items. */
export function dayHeading(iso: string): string {
  const d = new Date(iso);
  const day = dayKey.format(d);
  if (day === dayKey.format(new Date())) return "Hoy";
  if (day === dayKey.format(new Date(Date.now() - 86_400_000))) return "Ayer";
  return capitalise(fmtWeekday.format(d));
}

/** "Hoy", "Ayer", "14 oct": a day, short. */
export function dayShort(iso: string): string {
  const heading = dayHeading(iso);
  return heading === "Hoy" || heading === "Ayer" ? heading : fmtDay.format(new Date(iso));
}

export function duration(minutes: number): string {
  const m = Math.round(minutes);
  if (m < 60) return `${m} min`;
  const hours = Math.floor(m / 60);
  return m % 60 ? `${hours} h ${m % 60} min` : `${hours} h`;
}

export function ago(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return `hace ${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 3600) return `hace ${Math.round(seconds / 60)} min`;
  if (seconds < 6 * 3600) return `hace ${duration(seconds / 60)}`;
  return when(iso);
}

export function plural(n: number, one: string, many: string): string {
  return `${number(n)} ${n === 1 ? one : many}`;
}

export function metres(m: number): string {
  return m >= 1000 ? `${(m / 1000).toLocaleString("es-ES", { maximumFractionDigits: 1 })} km` : `${Math.round(m)} m`;
}

export function utc(iso: string): string {
  return `${new Date(iso).toISOString().slice(11, 16)} UTC`;
}

export function decimal(x: number, digits = 1): string {
  return x.toLocaleString("es-ES", { maximumFractionDigits: digits });
}
