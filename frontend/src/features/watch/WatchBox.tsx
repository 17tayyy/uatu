// On a vessel's card: why you watch it and a way to stop, or a way to start.
import { Eye } from "lucide-react";
import { useState } from "react";
import { useAddWatch, useRemoveWatch, useSession } from "../../api/queries";
import type { VesselDetail } from "../../api/types";
import { useUi } from "../../app/store";
import { C } from "../../lib/constants";
import { capitalise, fmtDay } from "../../lib/format";
import { Button, Callout, fieldClass, formErrorClass, LinkButton, Shape } from "../../ui";

const since = (iso: string) => `desde el ${fmtDay.format(new Date(iso))}`;

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
      <Callout tone={C.serious} icon={<Shape kind="arrow" color={C.serious} />} title="En tu lista de vigilancia">
        {v.watched.map((w) => (
          <span key={w.id}>
            {capitalise(w.note)} ({since(w.added_at)}).
          </span>
        ))}
        {user && (
          <span>
            <LinkButton
              disabled={remove.isPending}
              onClick={() =>
                Promise.all(v.watched.map((w) => remove.mutateAsync(w.id))).catch((e: Error) =>
                  setError(`No se pudo quitar: ${e.message}`),
                )
              }
            >
              Dejar de vigilar
            </LinkButton>
          </span>
        )}
        <p className={formErrorClass}>{error}</p>
      </Callout>
    );
  }
  if (!writing) {
    return (
      <div>
        <Button size="small" onClick={() => (user ? setWriting(true) : setLoginOpen(true))}>
          <Eye size={13} /> Vigilar este barco
        </Button>
      </div>
    );
  }
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return setError("Escribe por qué lo vigilas.");
    add.mutate(
      { ...(v.imo ? { imo: v.imo } : { mmsi: v.mmsi }), note: note.trim() },
      {
        onSuccess: () => setWriting(false),
        onError: (err) => setError(`No se pudo guardar: ${err.message}`),
      },
    );
  };
  return (
    <form onSubmit={save}>
      <Callout tone={C.serious} icon={<Shape kind="arrow" color={C.serious} />} title="Vigilar este barco">
        <textarea
          className={fieldClass}
          rows={2}
          maxLength={300}
          placeholder="Por qué lo vigilas (p. ej. «fondea frente a Ceuta sin AIS»)"
          aria-label="Por qué lo vigilas"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          // biome-ignore lint/a11y/noAutofocus: the form opens on purpose to write this
          autoFocus
        />
        <span style={{ fontSize: 12, color: "var(--text-3)" }}>
          {v.imo
            ? "Por su IMO: sigue valiendo aunque cambie de nombre, MMSI o bandera."
            : "Por su MMSI (no emite IMO)."}{" "}
          Te avisa al móvil cada vez que se le oiga en la costa.
        </span>
        <p className={formErrorClass}>{error}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <Button type="submit" variant="primary" size="small" disabled={add.isPending}>
            Vigilar
          </Button>
          <Button variant="ghost" size="small" onClick={() => setWriting(false)}>
            Cancelar
          </Button>
        </div>
      </Callout>
    </form>
  );
}
