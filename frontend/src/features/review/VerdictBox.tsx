// Your verdict on a detection: one click (or key) to give it, then which vessel and a note if you like.

import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { useDeleteReview, usePutReview, useSession } from "../../api/queries";
import type { Detection, ReviewWrite } from "../../api/types";
import { useUi } from "../../app/store";
import { VERDICT, VERDICT_KEYS, type VerdictKey } from "../../lib/constants";
import { when } from "../../lib/format";
import { Button, fieldClass, formErrorClass, Glyph, Kbd } from "../../ui";
import s from "./review.module.css";

export function VerdictBox({ detection: d, passId }: { detection: Detection; passId: string }) {
  const user = useSession().data?.username;
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const put = usePutReview();
  const remove = useDeleteReview();
  const review = d.review;
  const [editing, setEditing] = useState(false);
  const [mmsi, setMmsi] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setMmsi(String(d.review?.mmsi ?? ""));
    setNote(d.review?.note ?? "");
    setEditing(false);
    setError("");
  }, [d.review]);

  const give = (verdict: VerdictKey, extra: Partial<ReviewWrite> = {}) => {
    const body: ReviewWrite = { verdict };
    const keptMmsi = extra.mmsi ?? review?.mmsi;
    const keptNote = extra.note ?? review?.note;
    if (VERDICT[verdict].vessel && keptMmsi) body.mmsi = keptMmsi;
    if (keptNote) body.note = keptNote;
    setError("");
    put.mutate({ id: d.id, passId, body }, { onError: (e) => setError(`No se pudo guardar: ${e.message}`) });
  };

  const saveDetails = () => {
    if (!review) return;
    const typed = mmsi.trim();
    if (typed && !/^\d{1,9}$/.test(typed)) return setError("El MMSI tiene hasta 9 cifras.");
    const body: ReviewWrite = { verdict: review.verdict as VerdictKey };
    if (VERDICT[body.verdict as VerdictKey].vessel && typed) body.mmsi = Number(typed);
    if (note.trim()) body.note = note.trim();
    put.mutate(
      { id: d.id, passId, body },
      { onSuccess: () => setEditing(false), onError: (e) => setError(`No se pudo guardar: ${e.message}`) },
    );
  };

  const suggestion = d.mmsi ?? d.suspected_mmsi;
  const suggestionName = d.mmsi ? d.vessel_name : d.suspect_name;
  const current = review?.verdict as VerdictKey | undefined;

  if (!user) {
    return (
      <div className={s.extra}>
        {review && current && (
          <div className={s.given}>
            <Glyph kind={VERDICT[current].glyph} color={VERDICT[current].color} size={13} />
            <span style={{ color: "var(--text-2)" }}>{VERDICT[current].label}</span>
            <span>
              · {review.reviewed_by ?? "alguien"}, {when(review.reviewed_at)}
            </span>
          </div>
        )}
        <div>
          <Button size="small" onClick={() => setLoginOpen(true)}>
            Entrar para dar un veredicto
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={s.extra}>
      <div className={s.menu}>
        {VERDICT_KEYS.map((key, i) => (
          <button
            key={key}
            type="button"
            className={s.option}
            aria-pressed={current === key}
            disabled={put.isPending}
            onClick={() => give(key)}
          >
            <Glyph kind={VERDICT[key].glyph} color={VERDICT[key].color} size={14} />
            {VERDICT[key].label}
            <span className={s.optionHint}>{VERDICT[key].hint}</span>
            {current === key ? (
              <Check size={14} className={s.check} />
            ) : (
              <span className={s.optionKey}>
                <Kbd>{i + 1}</Kbd>
              </span>
            )}
          </button>
        ))}
      </div>
      {review && !editing && (
        <div className={s.given}>
          {review.mmsi && <span className={s.note}>Es {review.vessel_name ?? `MMSI ${review.mmsi}`}.</span>}
          {review.note && <span className={s.note}>«{review.note}»</span>}
          <span>
            {review.reviewed_by ?? "alguien"}, {when(review.reviewed_at)}
          </span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 2 }}>
            <Button size="small" variant="ghost" onClick={() => setEditing(true)}>
              {review.mmsi || review.note ? "Editar" : "Barco o nota"}
            </Button>
            <Button
              size="small"
              variant="ghost"
              onClick={() =>
                remove.mutate({ id: d.id, passId }, { onError: (e) => setError(`No se pudo quitar: ${e.message}`) })
              }
            >
              Quitar
            </Button>
          </span>
        </div>
      )}
      {review && editing && (
        <div className={s.extra}>
          {current && VERDICT[current].vessel && (
            <div className={s.row}>
              <input
                className={fieldClass}
                inputMode="numeric"
                maxLength={9}
                placeholder="MMSI del barco, si lo sabes"
                value={mmsi}
                onChange={(e) => setMmsi(e.target.value)}
                aria-label="MMSI del barco"
              />
              {suggestion && String(suggestion) !== mmsi && (
                <Button size="small" onClick={() => setMmsi(String(suggestion))} title="Usar el barco sugerido">
                  {suggestionName ?? suggestion}
                </Button>
              )}
            </div>
          )}
          <textarea
            className={fieldClass}
            rows={2}
            maxLength={500}
            placeholder="Nota: por qué"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Nota"
          />
          <div className={s.row}>
            <Button variant="primary" size="small" onClick={saveDetails} disabled={put.isPending}>
              Guardar
            </Button>
            <Button variant="ghost" size="small" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}
      <p className={formErrorClass} role="alert">
        {error}
      </p>
    </div>
  );
}

/** The keyboard on a detection: 1–4 give a verdict and move on, J/K move along the list, Esc closes. */
export function useReviewKeys(detection: Detection | undefined, passId: string | undefined) {
  const user = useSession().data?.username;
  const { mutate } = usePutReview();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || target?.closest("input, textarea, select, [contenteditable]")) return;
      const ui = useUi.getState();
      const list = ui.reviewList;
      const at = detection ? list.findIndex((item) => item.id === detection.id) : -1;
      const go = (step: number) => {
        const next = list[at + step];
        if (next) ui.openDetection(next);
      };
      if (e.key === "Escape") ui.select(null);
      else if (e.key === "j" || e.key === "ArrowDown") go(1);
      else if (e.key === "k" || e.key === "ArrowUp") go(-1);
      else if (/^[1-4]$/.test(e.key) && detection && passId) {
        if (!user) return ui.setLoginOpen(true);
        const verdict = VERDICT_KEYS[Number(e.key) - 1] as VerdictKey;
        const body: ReviewWrite = { verdict };
        if (detection.review?.note) body.note = detection.review.note;
        mutate({ id: detection.id, passId, body });
        if (at >= 0) go(list[at + 1] ? 1 : 0);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detection, passId, user, mutate]);
}
