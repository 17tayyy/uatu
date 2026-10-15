// Signing in: for verdicts and your watch list. Looking needs none.
import { useEffect, useRef, useState } from "react";
import { useLogin } from "../../api/queries";
import { useUi } from "../../app/store";
import { load, save } from "../../lib/storage";
import { Button, fieldClass, formErrorClass, labelClass } from "../../ui";
import s from "./auth.module.css";

export function LoginDialog() {
  const open = useUi((u) => u.loginOpen);
  const setOpen = useUi((u) => u.setLoginOpen);
  const dialog = useRef<HTMLDialogElement>(null);
  const login = useLogin();
  const [username, setUsername] = useState(() => load("uatu.user", ""));
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (open && !d.open) {
      setError("");
      setPassword("");
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    login.mutate(
      { username: username.trim(), password },
      {
        onSuccess: (session) => {
          save("uatu.user", session.username);
          setOpen(false);
        },
        onError: (err) =>
          setError(
            err.message === "Wrong username or password"
              ? "Usuario o contraseña incorrectos."
              : `No se pudo entrar: ${err.message}`,
          ),
      },
    );
  };

  return (
    <dialog ref={dialog} className={s.dialog} aria-labelledby="login-title" onClose={() => setOpen(false)}>
      <form className={s.form} onSubmit={submit}>
        <h2 id="login-title" className={s.title}>
          Entrar
        </h2>
        <p className={s.hint}>
          Para dar veredictos sobre las detecciones y cambiar tu lista de vigilancia. Para mirar no hace falta.
        </p>
        <label className={labelClass}>
          Usuario
          <input
            className={fieldClass}
            name="username"
            autoComplete="username"
            required
            maxLength={40}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className={labelClass}>
          Contraseña
          <input
            className={fieldClass}
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
          />
        </label>
        <p className={formErrorClass} role="alert">
          {error}
        </p>
        <div className={s.actions}>
          <Button type="submit" variant="primary" disabled={login.isPending}>
            Entrar
          </Button>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancelar
          </Button>
        </div>
      </form>
    </dialog>
  );
}
