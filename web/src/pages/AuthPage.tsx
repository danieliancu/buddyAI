import { useState, type FormEvent } from "react";
import { Watch } from "lucide-react";
import { api, ApiError } from "../api";
import { Button, ErrorBox, Field, Input, PasswordInput } from "../components/ui";

export default function AuthPage({ mode, onDone }: { mode: "setup" | "login"; onDone: (user: string) => void }) {
  const setup = mode === "setup";
  const [username, setUsername] = useState(setup ? "admin" : "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  /** The server asked for a two-factor code (authenticator or recovery code). */
  const [needCode, setNeedCode] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (username.trim().length < 3) return setError(new Error("Username must be at least 3 characters."));
    if (password.length < 8) return setError(new Error("Password must be at least 8 characters."));
    if (setup && password !== confirm) return setError(new Error("Passwords do not match."));
    if (needCode && code.trim().length < 6) return setError(new Error("Enter the 6-digit code from your authenticator app, or a recovery code."));
    setBusy(true);
    try {
      const r = setup
        ? await api.auth.setup(username.trim(), password)
        : await api.auth.login(username.trim(), password, needCode ? code.trim() : undefined);
      onDone(r.user);
    } catch (err) {
      if (err instanceof ApiError && err.mfaRequired) {
        if (needCode) setError(new Error("That code is not valid. Try again."));
        setNeedCode(true);
        setCode("");
      } else if (err instanceof ApiError && err.status === 401) {
        setNeedCode(false);
        setCode("");
        setError(new Error("Wrong username or password."));
      } else if (err instanceof ApiError && err.status === 409) setError(new Error("The admin account already exists. Reload the page."));
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
            <h1 className="text-lg font-semibold">olá</h1>
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
            <Input
              id="u"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus={!needCode}
              readOnly={needCode}
            />
          </Field>
          <Field label="Password" htmlFor="p" hint={setup ? "At least 8 characters" : undefined}>
            <PasswordInput
              id="p"
              autoComplete={setup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              readOnly={needCode}
            />
          </Field>
          {setup && (
            <Field label="Confirm password" htmlFor="p2">
              <PasswordInput id="p2" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            </Field>
          )}
          {needCode && (
            <Field
              label="Two-factor code"
              htmlFor="code"
              hint="The 6-digit code from your authenticator app, or one of your recovery codes."
            >
              <Input
                id="code"
                autoComplete="one-time-code"
                inputMode="text"
                maxLength={32}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="font-mono tracking-widest"
                autoFocus
              />
            </Field>
          )}
          <ErrorBox error={error} />
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            {setup ? "Create account" : needCode ? "Verify" : "Sign in"}
          </Button>
          {needCode && (
            <button
              type="button"
              className="w-full text-center text-xs text-muted hover:text-fg"
              onClick={() => {
                setNeedCode(false);
                setCode("");
                setPassword("");
                setError(null);
              }}
            >
              Use another account
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
