import { LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../lib/api";
import type { AuthState } from "../types";

/** Sign-in with a local account (CIPW-62); shown when auth.mode is local and nobody is signed in. */
export function SignIn({ onSignedIn }: { onSignedIn: (auth: AuthState) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    api
      .login(name.trim(), password)
      .then(onSignedIn)
      .catch((err: Error) => {
        setError(err.message);
        setPassword("");
      })
      .finally(() => setBusy(false));
  };

  return (
    <main className="signin">
      <form className="card signin-card" onSubmit={submit} aria-labelledby="signin-title">
        <img className="logo logo-light" src="/fides-logo.svg" alt="Fides" />
        <img className="logo logo-dark" src="/fides-logo-white.svg" alt="" />
        <h1 id="signin-title">Sign in</h1>
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit" className="btn primary block" disabled={busy || !name.trim() || !password}>
          <LogIn aria-hidden="true" />
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="faint" style={{ margin: 0, fontSize: 13 }}>No account yet? Ask the Fides admin of your team.</p>
      </form>
    </main>
  );
}
