// The time machine's controls: a moment since recording began, played back at 10 s to 30 min a second.
// Passes are marked on the track (a click goes there) and Uatu's own outages shaded.
import { History, Pause, Play, X } from "lucide-react";
import { useHistoryRange, usePasses } from "../../api/queries";
import { useUi } from "../../app/store";
import { number, when } from "../../lib/format";
import { useTimeInfo } from "../../map/TimeSync";
import { HOUR, SPEEDS } from "../../map/time";
import s from "./time.module.css";

export function Timebar() {
  const time = useUi((u) => u.time);
  const zone = useUi((u) => u.zone);
  const enterTime = useUi((u) => u.enterTime);
  const setTime = useUi((u) => u.setTime);
  const setPlaying = useUi((u) => u.setPlaying);
  const setSpeed = useUi((u) => u.setSpeed);
  const exitTime = useUi((u) => u.exitTime);
  const info = useTimeInfo();
  const range = useHistoryRange(time != null).data;
  const passes = usePasses(zone).data ?? [];

  if (!time) {
    return (
      <button type="button" className={s.open} onClick={() => enterTime(Date.now() - HOUR)}>
        <History size={15} /> Máquina del tiempo
      </button>
    );
  }

  const from = range ? new Date(range.start).getTime() : Date.now() - 7 * 24 * HOUR;
  const to = Date.now();
  const at = (t: number) => `${(100 * (t - from)) / (to - from)}%`;
  const outage = range?.outages.find(
    (o) => new Date(o.start).getTime() <= time.t && time.t <= new Date(o.end).getTime(),
  );

  const play = () => {
    if (!time.playing && time.t >= Date.now() - 60_000) setTime(Date.now() - 6 * HOUR); // at the end: from six hours ago
    setPlaying(!time.playing);
  };

  return (
    <section className={s.bar} aria-label="Máquina del tiempo">
      <div className={s.top}>
        <button type="button" className={s.play} onClick={play} aria-label={time.playing ? "Pausa" : "Reproducir"}>
          {time.playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button type="button" className={s.step} onClick={() => setTime(time.t - 10 * 60_000)} title="10 min antes">
          −10 min
        </button>
        <span className={s.label}>{when(time.t, time.t % 60_000 !== 0)}</span>
        <button type="button" className={s.step} onClick={() => setTime(time.t + 10 * 60_000)} title="10 min después">
          +10 min
        </button>
        <span className={s.grow} />
        <select
          className={s.speed}
          value={time.speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          aria-label="Velocidad"
        >
          {SPEEDS.map((x) => (
            <option key={x} value={x}>
              {x < 60 ? `1 s = ${x} s` : `1 s = ${x / 60} min`}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={s.close}
          onClick={exitTime}
          aria-label="Volver al presente"
          title="Volver al presente"
        >
          <X size={16} />
        </button>
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
      <div className={s.count} data-warning={outage ? true : undefined}>
        {outage
          ? `⚠ Uatu no grababa AIS de ${when(outage.start)} a ${when(outage.end)} (sin conexión): lo que falta no es un barco callado`
          : info.loading
            ? "Cargando el AIS de ese momento…"
            : info.vessels != null
              ? `${number(info.vessels)} barcos oídos en la última media hora · las marcas son pasadas del radar`
              : ""}
      </div>
    </section>
  );
}
