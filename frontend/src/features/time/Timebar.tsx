// The time machine, docked under the map: a moment since recording began, played at 10 s to 30 min a
// second. Passes are marked on the track (a click goes there), Uatu's own outages hatched.
import { Pause, Play, X } from "lucide-react";
import { useHistoryRange, usePasses } from "../../api/queries";
import { useUi } from "../../app/store";
import { number, when } from "../../lib/format";
import { useTimeInfo } from "../../map/TimeSync";
import { HOUR, SPEEDS } from "../../map/time";
import { Button } from "../../ui";
import s from "./time.module.css";

export function Timebar() {
  const time = useUi((u) => u.time);
  const zone = useUi((u) => u.zone);
  const setTime = useUi((u) => u.setTime);
  const setPlaying = useUi((u) => u.setPlaying);
  const setSpeed = useUi((u) => u.setSpeed);
  const exitTime = useUi((u) => u.exitTime);
  const info = useTimeInfo();
  const range = useHistoryRange(time != null).data;
  const passes = usePasses(zone).data ?? [];
  if (!time) return null;

  const from = range ? new Date(range.start).getTime() : Date.now() - 7 * 24 * HOUR;
  const to = Date.now();
  const at = (t: number) => `${(100 * (t - from)) / (to - from)}%`;
  const outage = range?.outages.find(
    (o) => new Date(o.start).getTime() <= time.t && time.t <= new Date(o.end).getTime(),
  );
  const play = () => {
    if (!time.playing && time.t >= Date.now() - 60_000) setTime(Date.now() - 6 * HOUR); // at the end: from 6 h ago
    setPlaying(!time.playing);
  };

  return (
    <section className={s.bar} aria-label="Máquina del tiempo">
      <Button icon variant="primary" onClick={play} aria-label={time.playing ? "Pausa" : "Reproducir"}>
        {time.playing ? <Pause size={14} /> : <Play size={14} />}
      </Button>
      <div className={s.when}>
        <span className={`${s.label} mono`}>{when(time.t, true)}</span>
        <span className={s.count} data-warning={outage ? true : undefined}>
          {outage
            ? `Uatu sin grabar hasta ${when(outage.end)}: lo que falta no es un barco callado`
            : info.loading
              ? "cargando…"
              : info.vessels != null
                ? `${number(info.vessels)} barcos`
                : ""}
        </span>
      </div>
      <div className={s.track}>
        {range?.outages.map((o) => (
          <span
            key={o.start}
            className={s.outage}
            style={{
              left: at(new Date(o.start).getTime()),
              width: `calc(${at(new Date(o.end).getTime())} - ${at(new Date(o.start).getTime())})`,
            }}
            title={`Uatu sin grabar: ${when(o.start)} – ${when(o.end)}`}
          />
        ))}
        {passes
          .filter((p) => new Date(p.acquired_at).getTime() >= from)
          .map((p) => (
            <button
              type="button"
              key={p.id}
              className={s.passTick}
              style={{ left: at(new Date(p.acquired_at).getTime()) }}
              onClick={() => setTime(new Date(p.acquired_at).getTime())}
              title={`Pasada de ${when(p.acquired_at)}`}
              aria-label={`Ir a la pasada de ${when(p.acquired_at)}`}
            />
          ))}
        <input
          type="range"
          className={s.slider}
          min={Math.floor(from / 60_000) * 60_000}
          max={to}
          step={60_000}
          value={time.t}
          onChange={(e) => setTime(Number(e.target.value))}
          aria-label="Momento"
        />
      </div>
      <Button size="small" variant="ghost" onClick={() => setTime(time.t - 10 * 60_000)} title="10 min antes">
        −10′
      </Button>
      <Button size="small" variant="ghost" onClick={() => setTime(time.t + 10 * 60_000)} title="10 min después">
        +10′
      </Button>
      <select
        className={s.speed}
        value={time.speed}
        onChange={(e) => setSpeed(Number(e.target.value))}
        aria-label="Velocidad"
      >
        {SPEEDS.map((x) => (
          <option key={x} value={x}>
            {x < 60 ? `×${x}` : `×${x / 60} min/s`}
          </option>
        ))}
      </select>
      <Button
        icon
        size="small"
        variant="ghost"
        onClick={exitTime}
        aria-label="Volver al presente"
        title="Volver al presente"
      >
        <X size={14} />
      </Button>
    </section>
  );
}
