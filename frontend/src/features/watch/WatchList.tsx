// Your own watch list: an alert to the phone whenever one of its vessels is heard on the coast.
import { Eye, Plus } from "lucide-react";
import { useState } from "react";
import { useAddWatch, useRemoveWatch, useSession, useWatches } from "../../api/queries";
import type { WatchCreate, WatchRead } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { ago, capitalise, fmtDay } from "../../lib/format";
import { Button, Empty, ErrorNote, fieldClass, formErrorClass, LinkButton, Loading, labelClass } from "../../ui";
import p from "../panel.module.css";
import s from "./watch.module.css";

function WatchRow({ w, canEdit }: { w: WatchRead; canEdit: boolean }) {
  const remove = useRemoveWatch();
  const [heard, ...others] = w.vessels;
  const title = heard?.name ?? w.name ?? (w.imo ? `IMO ${w.imo}` : `MMSI ${w.mmsi}`);
  const by = [w.imo && `IMO ${w.imo}`, w.mmsi && `MMSI ${w.mmsi}`, w.name && `nombre «${w.name}»`]
    .filter(Boolean)
    .join(" · ");
  return (
    <li>
      <div className={p.row}>
        <div className={p.rowTop}>
          {heard ? (
            <button
              type="button"
              className={`${p.rowTitle} ${s.titleLink}`}
              onClick={() => navigate(`vessels/${heard.mmsi}`)}
            >
              {title}
            </button>
          ) : (
            <span className={p.rowTitle}>{title}</span>
          )}
          <span className={p.rowTime}>{heard?.last_seen_at ? `oído ${ago(heard.last_seen_at)}` : "aún no oído"}</span>
        </div>
        <div className={p.rowText}>{capitalise(w.note)}</div>
        <div className={p.rowMeta}>
          Por {by} · desde el {fmtDay.format(new Date(w.added_at))}
          {others.length > 0 && ` · coincide también con ${others.map((o) => o.name ?? `MMSI ${o.mmsi}`).join(", ")}`}
        </div>
        {canEdit && (
          <div>
            <LinkButton disabled={remove.isPending} onClick={() => remove.mutate(w.id)}>
              Quitar
            </LinkButton>
          </div>
        )}
      </div>
    </li>
  );
}

function AddForm({ onDone }: { onDone: () => void }) {
  const add = useAddWatch();
  const [imo, setImo] = useState("");
  const [mmsi, setMmsi] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    const body: WatchCreate = { note: note.trim() };
    if (imo.trim() && !/^\d{7}$/.test(imo.trim())) return setError("El IMO tiene 7 cifras.");
    if (mmsi.trim() && !/^\d{1,9}$/.test(mmsi.trim())) return setError("El MMSI tiene hasta 9 cifras.");
    if (imo.trim()) body.imo = Number(imo.trim());
    if (mmsi.trim()) body.mmsi = Number(mmsi.trim());
    if (name.trim()) body.name = name.trim();
    if (!body.imo && !body.mmsi && !body.name) return setError("Pon un IMO, un MMSI o un nombre.");
    if (!body.note) return setError("Escribe por qué lo vigilas.");
    add.mutate(body, { onSuccess: onDone, onError: (err) => setError(`No se pudo guardar: ${err.message}`) });
  };
  return (
    <form className={s.form} onSubmit={save} noValidate>
      <div className={s.fields}>
        <label className={labelClass}>
          IMO
          <input
            className={fieldClass}
            inputMode="numeric"
            maxLength={7}
            placeholder="7 cifras"
            value={imo}
            onChange={(e) => setImo(e.target.value)}
          />
        </label>
        <label className={labelClass}>
          MMSI
          <input
            className={fieldClass}
            inputMode="numeric"
            maxLength={9}
            placeholder="9 cifras"
            value={mmsi}
            onChange={(e) => setMmsi(e.target.value)}
          />
        </label>
        <label className={`${labelClass} ${s.wide}`}>
          Nombre
          <input
            className={fieldClass}
            maxLength={40}
            placeholder="como lo emite"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>
      <textarea
        className={fieldClass}
        rows={2}
        maxLength={300}
        placeholder="Por qué lo vigilas"
        aria-label="Por qué lo vigilas"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <p className={s.hint}>Basta uno: el IMO sigue al casco aunque cambie de nombre, MMSI o bandera.</p>
      <p className={formErrorClass}>{error}</p>
      <div className={s.actions}>
        <Button type="submit" variant="primary" size="small" disabled={add.isPending}>
          Vigilar
        </Button>
        <Button variant="ghost" size="small" onClick={onDone}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

export function WatchList() {
  const watches = useWatches();
  const user = useSession().data?.username;
  const setLoginOpen = useUi((u) => u.setLoginOpen);
  const [adding, setAdding] = useState(false);
  return (
    <>
      <div className={p.header}>
        <div className={p.eyebrow}>Tuya</div>
        <h1 className={p.title}>Lista de vigilancia</h1>
        <div className={p.subtitle}>
          Te avisa al móvil cada vez que se oiga en la costa a uno de estos barcos. No sale en los informes.
        </div>
      </div>
      <div className={p.cta}>
        {user ? (
          !adding && (
            <Button onClick={() => setAdding(true)}>
              <Plus size={14} /> Añadir por IMO, MMSI o nombre
            </Button>
          )
        ) : (
          <Button onClick={() => setLoginOpen(true)}>Entra para cambiar tu lista</Button>
        )}
      </div>
      {adding && <AddForm onDone={() => setAdding(false)} />}
      {watches.isPending ? (
        <Loading />
      ) : watches.isError ? (
        <ErrorNote error={watches.error} />
      ) : watches.data.length === 0 ? (
        <Empty>
          <Eye size={26} />
          Vacía. Añade barcos aquí o desde su ficha («Vigilar este barco»).
        </Empty>
      ) : (
        <ul className={p.list}>
          {watches.data.map((w) => (
            <WatchRow key={w.id} w={w} canEdit={Boolean(user)} />
          ))}
        </ul>
      )}
    </>
  );
}
