// Your own watch list: an alert to the phone whenever one of its vessels is heard on the coast.
import { Eye, Plus, X } from "lucide-react";
import { useState } from "react";
import { useAddWatch, useRemoveWatch, useSession, useWatches } from "../../api/queries";
import type { WatchCreate, WatchRead } from "../../api/types";
import { navigate } from "../../app/router";
import { useUi } from "../../app/store";
import { C } from "../../lib/constants";
import { ago, capitalise } from "../../lib/format";
import {
  Button,
  Empty,
  ErrorNote,
  fieldClass,
  formErrorClass,
  Glyph,
  Group,
  Header,
  Loading,
  labelClass,
} from "../../ui";
import p from "../panel.module.css";
import s from "./watch.module.css";

function WatchRow({ w, canEdit }: { w: WatchRead; canEdit: boolean }) {
  const remove = useRemoveWatch();
  const [heard] = w.vessels;
  const title = heard?.name ?? w.name ?? (w.imo ? `IMO ${w.imo}` : `MMSI ${w.mmsi}`);
  const by = [w.imo && `IMO ${w.imo}`, w.mmsi && `MMSI ${w.mmsi}`, w.name && `«${w.name}»`].filter(Boolean).join(", ");
  return (
    <li className={s.rowWrap}>
      <button
        type="button"
        className={p.row}
        disabled={!heard}
        onClick={() => heard && navigate(`vessels/${heard.mmsi}`)}
        title={`Por ${by}`}
      >
        <Glyph kind={heard?.last_seen_at ? "dot" : "ring"} color={C.serious} size={13} />
        <span className={p.main}>
          <span className={p.title}>{title}</span>
          <span className={p.meta}>{capitalise(w.note)}</span>
        </span>
        <span className={p.time}>{heard?.last_seen_at ? ago(heard.last_seen_at) : "sin oír"}</span>
      </button>
      {canEdit && (
        <Button
          icon
          size="small"
          variant="ghost"
          className={s.remove}
          disabled={remove.isPending}
          onClick={() => remove.mutate(w.id)}
          title="Quitar"
          aria-label={`Quitar ${title}`}
        >
          <X size={13} />
        </Button>
      )}
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
    <form className={`${s.form} ${s.addForm}`} onSubmit={save} noValidate>
      <div className={s.fields}>
        <label className={labelClass}>
          IMO
          <input
            className={fieldClass}
            inputMode="numeric"
            maxLength={7}
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
            value={mmsi}
            onChange={(e) => setMmsi(e.target.value)}
          />
        </label>
        <label className={`${labelClass} ${s.wide}`}>
          Nombre
          <input className={fieldClass} maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className={`${labelClass} ${s.wide}`}>
          Por qué
          <textarea
            className={fieldClass}
            rows={2}
            maxLength={300}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      </div>
      <p className={s.hint}>Basta uno. El IMO sigue al casco aunque cambie de nombre, MMSI o bandera.</p>
      <p className={formErrorClass}>{error}</p>
      <div className={s.actions}>
        <Button type="submit" size="small" variant="primary" disabled={add.isPending}>
          Vigilar
        </Button>
        <Button size="small" variant="ghost" onClick={onDone}>
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
    <div className={p.panel}>
      <Header
        crumbs={[["Vigilancia", null]]}
        actions={
          <Button size="small" onClick={() => (user ? setAdding(true) : setLoginOpen(true))} disabled={adding}>
            <Plus size={13} /> Añadir
          </Button>
        }
      />
      <div className={p.scroll}>
        {adding && <AddForm onDone={() => setAdding(false)} />}
        {watches.isPending ? (
          <Loading />
        ) : watches.isError ? (
          <ErrorNote error={watches.error} />
        ) : watches.data.length === 0 ? (
          <Empty icon={<Eye size={22} />}>
            Tu lista está vacía. Aviso al móvil cuando se oiga a uno de sus barcos.
          </Empty>
        ) : (
          <Group title="Tu lista" count={watches.data.length}>
            <ul className={p.list}>
              {watches.data.map((w) => (
                <WatchRow key={w.id} w={w} canEdit={Boolean(user)} />
              ))}
            </ul>
          </Group>
        )}
      </div>
    </div>
  );
}
