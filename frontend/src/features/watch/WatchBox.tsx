// On a vessel's page: why you watch it and a way to stop, or a way to start.
import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { useAddWatch, useRemoveWatch, useSession } from "../../api/queries";
import type { VesselDetail } from "../../api/types";
import { useUi } from "../../app/store";
import { capitalise, fmtDay } from "../../lib/format";
import { Button, fieldClass, formErrorClass } from "../../ui";
import s from "./watch.module.css";

export function WatchBox({ vessel: v }: { vessel: VesselDetail }) {
  const user = useSession().data?.username;
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const add = useAddWatch();
  const remove = useRemoveWatch();
  const [writing, setWriting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  if (v.watched.length) {
    return (
      <div className={s.watched}>
        <Eye size={14} className={s.eye} />
        <span className={s.watchedText}>
          {v.watched.map((w) => `${capitalise(w.note)} (desde el ${fmtDay.format(new Date(w.added_at))})`).join(" · ")}
        </span>
        {user && (
          <Button
            size="small"
            variant="ghost"
            disabled={remove.isPending}
            onClick={() =>
              Promise.all(v.watched.map((w) => remove.mutateAsync(w.id))).catch((e: Error) =>
                setError(`No se pudo quitar: ${e.message}`),
              )
            }
            title="Dejar de vigilar"
          >
            <EyeOff size={13} /> Dejar
          </Button>
        )}
        <p className={formErrorClass}>{error}</p>
      </div>
    );
  }
  if (!writing) {
    return (
      <div>
        <Button size="small" onClick={() => (user ? setWriting(true) : setLoginOpen(true))}>
          <Eye size={13} /> Vigilar
        </Button>
      </div>
    );
  }
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return setError("Escribe por qué lo vigilas.");
    add.mutate(
      { ...(v.imo ? { imo: v.imo } : { mmsi: v.mmsi }), note: note.trim() },
      { onSuccess: () => setWriting(false), onError: (err) => setError(`No se pudo guardar: ${err.message}`) },
    );
  };
  return (
    <form className={s.form} onSubmit={save}>
      <textarea
        className={fieldClass}
        rows={2}
        maxLength={300}
        placeholder="Por qué lo vigilas"
        aria-label="Por qué lo vigilas"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        // biome-ignore lint/a11y/noAutofocus: the form opens to write this
        autoFocus
      />
      <p className={s.hint}>
        {v.imo ? "Por su IMO, aunque cambie de nombre, MMSI o bandera." : "Por su MMSI (no emite IMO)."} Aviso al móvil
        cada vez que se le oiga.
      </p>
      <p className={formErrorClass}>{error}</p>
      <div className={s.actions}>
        <Button type="submit" size="small" variant="primary" disabled={add.isPending}>
          Vigilar
        </Button>
        <Button size="small" variant="ghost" onClick={() => setWriting(false)}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}
