// The time machine on the map: every vessel where it was at the moment shown, with its last hour behind
// it, the events going on then and the radar pass of that moment. Reports come an hour at a time.
import { useQueries } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { create } from "zustand";
import { historyChunk, useEvents, useHistoryRange, usePasses } from "../api/queries";
import type { HistoryChunk } from "../api/types";
import { setRecordingFrom, useUi } from "../app/store";
import { EMPTY, setData } from "./instance";
import { passNear } from "./passes";
import { frameAt, HOUR, hoursAround } from "./time";

const combine = (results: { data?: unknown; isPending: boolean }[]) => ({
  tracks: results.map((r) => (r.data as HistoryChunk | undefined)?.tracks ?? []),
  loading: results.some((r) => r.isPending),
});

/** What the time bar says of the moment: vessels drawn, or still loading. */
export const useTimeInfo = create<{ vessels: number | null; loading: boolean }>(() => ({
  vessels: null,
  loading: false,
}));

export function TimeSync() {
  const time = useUi((s) => s.time);
  const zone = useUi((s) => s.zone);
  const minSeverity = useUi((s) => s.eventFilter.minSeverity);
  const showPass = useUi((s) => s.showPass);
  const setTime = useUi((s) => s.setTime);
  const setPlaying = useUi((s) => s.setPlaying);
  const on = time != null;

  const range = useHistoryRange(on);
  useEffect(() => {
    if (range.data) setRecordingFrom(new Date(range.data.start).getTime());
  }, [range.data]);

  const passes = usePasses(zone);
  const since = range.data ? new Date(range.data.start).getTime() : Date.now() - 7 * 24 * HOUR;
  const hours = Math.min(24 * 30, Math.ceil((Date.now() - since) / HOUR) + 1);
  const events = useEvents(zone, on ? hours : 48, minSeverity);

  const keys = useMemo(() => (time ? hoursAround(time.t) : []), [time]);
  const { tracks, loading } = useQueries({
    queries: keys.map((k) => ({ ...historyChunk(zone, k), enabled: on })),
    combine,
  });
  const t = time?.t ?? null;

  useEffect(() => {
    if (t == null) {
      setData("time-trails", EMPTY);
      useTimeInfo.setState({ vessels: null, loading: false });
      return;
    }
    const frame = frameAt(tracks, t);
    setData("vessels", { type: "FeatureCollection", features: frame.vessels });
    setData("time-trails", { type: "FeatureCollection", features: frame.trails });
    useTimeInfo.setState({ vessels: frame.vessels.length, loading });
  }, [t, tracks, loading]);

  // The events going on at that moment; an instant (an identity change) shows for half an hour.
  useEffect(() => {
    if (t == null || !events.data) return;
    const going = events.data.features.filter((f) => {
      const start = new Date(f.properties.start_at).getTime();
      const end = f.properties.end_at ? new Date(f.properties.end_at).getTime() : Date.now();
      return start <= t && t <= Math.max(end, start + 30 * 60_000);
    });
    setData("events", { type: "FeatureCollection", features: going });
  }, [t, events.data]);

  // The radar pass of the moment, if a satellite passed within 45 minutes of it.
  useEffect(() => {
    if (t == null || !passes.data) return;
    const near = passNear(t, passes.data);
    showPass(near?.id ?? null, near ? null : "Máquina del tiempo: ningún satélite pasó a esa hora");
  }, [t, passes.data, showPass]);

  // Playing: the moment moves on at the chosen speed, redrawn some eight times a second.
  const playing = time?.playing ?? false;
  const speed = time?.speed ?? 60;
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let drawn = 0;
    let frame = requestAnimationFrame(function step(now) {
      const current = useUi.getState().time;
      if (!current?.playing) return;
      if (now - drawn > 125) {
        const next = current.t + (now - last) * speed;
        last = now;
        drawn = now;
        if (next >= Date.now() - 1000) {
          setTime(Date.now());
          setPlaying(false);
          return;
        }
        setTime(next);
      }
      frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, setTime, setPlaying]);

  return null;
}
