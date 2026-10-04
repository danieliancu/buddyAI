import { useState, type FormEvent } from "react";
import { Watch } from "lucide-react";
import { api, ApiError } from "../api";
import { Button, ErrorBox, Field, Input } from "../components/ui";

export default function AuthPage({ mode, onDone }: { mode: "setup" | "login"; onDone: (user: string) => void }) {
  const setup = mode === "setup";
  const [username, setUsername] = useState(setup ? "admin" : "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (username.trim().length < 3) return setError(new Error("Username must be at least 3 characters."));
    if (password.length < 8) return setError(new Error("Password must be at least 8 characters."));
    if (setup && password !== confirm) return setError(new Error("Passwords do not match."));
    setBusy(true);
    try {
      const r = setup ? await api.auth.setup(username.trim(), password) : await api.auth.login(username.trim(), password);
      onDone(r.user);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError(new Error("Wrong username or password."));
      else if (err instanceof ApiError && err.status === 409) setError(new Error("The admin account already exists. Reload the page."));
      else setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-ui grid min-h-screen place-items-center bg-bg px-4 py-10 text-fg">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-xl">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-accent-bg text-accent">
            <Watch className="size-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">ola</h1>
            <p className="text-xs text-muted">{setup ? "First-time setup" : "Admin sign-in"}</p>
          </div>
        </div>
        {setup && (
          <p className="mb-4 text-sm text-muted">
            Create the admin account. The password protects access to watches, history and API keys.
          </p>
        )}
        <div className="space-y-4">
          <Field label="Username" htmlFor="u">
            <Input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
          </Field>
          <Field label="Password" htmlFor="p" hint={setup ? "At least 8 characters" : undefined}>
            <Input
              id="p"
              type="password"
              autoComplete={setup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          {setup && (
            <Field label="Confirm password" htmlFor="p2">
              <Input id="p2" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
          )}
          <ErrorBox error={error} />
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            {setup ? "Create account" : "Sign in"}
          </Button>
        </div>
      </form>
    </div>
  );
}
