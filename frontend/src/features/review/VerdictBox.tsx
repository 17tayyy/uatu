// Your verdict on a detection: one click (or key) to give it, then which vessel and a note if you like.
import { useEffect, useState } from "react";
import { useDeleteReview, usePutReview, useSession } from "../../api/queries";
import type { Detection, ReviewWrite } from "../../api/types";
import { useUi } from "../../app/store";
import { VERDICT, VERDICT_KEYS, type VerdictKey } from "../../lib/constants";
import { when } from "../../lib/format";
import { Button, fieldClass, formErrorClass, LinkButton, Shape, Tag } from "../../ui";
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
      <div className={s.given}>
        {review && current && (
          <div className={s.givenHead}>
            <Tag shape="square" color={VERDICT[current].color}>
              {VERDICT[current].label}
            </Tag>
            <span className={s.meta}>
              {review.reviewed_by ?? "alguien"}, {when(review.reviewed_at)}
            </span>
          </div>
        )}
        <LinkButton onClick={() => setLoginOpen(true)}>Entra para dar tu veredicto</LinkButton>
      </div>
    );
  }

  return (
    <div className={s.given}>
      <div className={s.verdicts}>
        {VERDICT_KEYS.map((key, i) => (
          <button
            key={key}
            type="button"
            className={s.verdict}
            style={{ "--tone": VERDICT[key].color } as React.CSSProperties}
            aria-pressed={current === key}
            disabled={put.isPending}
            onClick={() => give(key)}
          >
            <span className={s.verdictTop}>
              <Shape kind="square" color={VERDICT[key].color} size={10} />
              {VERDICT[key].label}
              <span className={s.verdictKey}>{i + 1}</span>
            </span>
            <span className={s.verdictHint}>{VERDICT[key].hint}</span>
          </button>
        ))}
      </div>
      {review && (
        <>
          {!editing && (
            <div className={s.meta}>
              {review.mmsi && <>Es {review.vessel_name ?? `MMSI ${review.mmsi}`}. </>}
              {review.note && <>«{review.note}» </>}
              {review.reviewed_by ?? "Alguien"}, {when(review.reviewed_at)} ·{" "}
              <LinkButton onClick={() => setEditing(true)}>
                {review.mmsi || review.note ? "Cambiar barco o nota" : "Añadir barco o nota"}
              </LinkButton>{" "}
              ·{" "}
              <LinkButton
                onClick={() =>
                  remove.mutate({ id: d.id, passId }, { onError: (e) => setError(`No se pudo quitar: ${e.message}`) })
                }
              >
                Quitar
              </LinkButton>
            </div>
          )}
          {editing && (
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
                    <Button size="small" onClick={() => setMmsi(String(suggestion))}>
                      {suggestionName ?? suggestion}
                    </Button>
                  )}
                </div>
              )}
              <textarea
                className={fieldClass}
                rows={2}
                maxLength={500}
                placeholder="Nota (opcional): por qué"
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
        </>
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
