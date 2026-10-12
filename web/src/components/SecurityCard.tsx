import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { AlertTriangle, Copy, LogOut, ShieldCheck } from "lucide-react";
import { api, type MfaSetup } from "../api";
import { useOperator } from "../operator";
import { Badge, Button, Card, ErrorBox, Field, Input, PasswordInput } from "./ui";

/** The operator's own security: two-factor authentication (TOTP) and "sign out everywhere". */
export default function SecurityCard() {
  const { me, refresh } = useOperator();
  const navigate = useNavigate();
  const [pending, setPending] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const signOutEverywhere = () =>
    run(async () => {
      if (!window.confirm("Sign out of the operator area in every browser, including this one?")) return;
      await api.auth.logoutAll();
      navigate("/admin/login", { replace: true });
    });

  return (
    <Card title="Security" icon={<ShieldCheck />}>
      <div className="space-y-5 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted">Two-factor authentication</span>
          {me?.mfa_enabled ? <Badge tone="ok">on</Badge> : <Badge tone={me?.mfa_required ? "danger" : "warn"}>off</Badge>}
          {me?.mfa_enabled && <span className="text-xs text-muted">{me.recovery_codes_left} recovery codes left</span>}
          {me && <span className="ml-auto text-xs text-muted">Role: {me.role}</span>}
        </div>
        {me?.mfa_setup_needed && (
          <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2 text-warn">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            This server requires two-factor authentication. Set it up to continue.
          </p>
        )}

        {codes ? (
          // The operator is re-read only after the codes were saved (a required enrolment would leave this screen).
          <RecoveryCodes
            codes={codes}
            onDone={() => {
              setCodes(null);
              refresh();
            }}
          />
        ) : me?.mfa_enabled ? (
          <DisableForm
            busy={busy}
            onSubmit={(password, code) =>
              run(async () => {
                await api.auth.mfa.disable(password, code);
                refresh();
              })
            }
          />
        ) : pending ? (
          <EnableForm
            setup={pending}
            busy={busy}
            onCancel={() => setPending(null)}
            onSubmit={(code) => run(async () => {
              const r = await api.auth.mfa.enable(code);
              setPending(null);
              setCodes(r.recovery_codes);
            })}
          />
        ) : (
          <div className="space-y-2">
            <p className="text-muted">
              Protect this operator account with a 6-digit code from an authenticator app (Google Authenticator, Microsoft
              Authenticator, 1Password, …) in addition to the password.
            </p>
            <Button variant="primary" loading={busy} onClick={() => run(async () => setPending(await api.auth.mfa.setup()))}>
              Set up two-factor authentication
            </Button>
          </div>
        )}
        <ErrorBox error={error} />

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
          <span className="text-muted">Lost a device or left a browser signed in? End every operator session of yours.</span>
          <Button icon={<LogOut className="size-3.5" />} loading={busy} onClick={signOutEverywhere}>
            Sign out everywhere
          </Button>
        </div>
      </div>
    </Card>
  );
}

function EnableForm({ setup, busy, onSubmit, onCancel }: { setup: MfaSetup; busy: boolean; onSubmit: (code: string) => void; onCancel: () => void }) {
  const [code, setCode] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (code.trim()) onSubmit(code.trim());
  };
  return (
    <form onSubmit={submit} className="space-y-3">
      <p>
        1. In your authenticator app, add an account with this key (choose “enter a setup key”, time-based):
      </p>
      <code className="block rounded-lg bg-surface-2 px-3 py-2 font-mono text-sm break-all select-all">{setup.secret}</code>
      <details className="text-xs text-muted">
        <summary className="cursor-pointer">Setup link (otpauth URI)</summary>
        <code className="mt-1 block font-mono break-all select-all">{setup.otpauth_uri}</code>
      </details>
      <Field label="2. Enter the 6-digit code the app shows" htmlFor="mfa-code">
        <div className="flex gap-2">
          <Input
            id="mfa-code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="w-32 font-mono tracking-widest"
          />
          <Button type="submit" variant="primary" loading={busy}>
            Turn on
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </Field>
    </form>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
    } catch {
      /* the codes stay selectable */
    }
  };
  return (
    <div className="space-y-3">
      <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-bg px-3 py-2 text-warn">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        Two-factor authentication is on. Save these recovery codes somewhere safe now — they are shown only once. Each
        code signs you in once if you lose your phone.
      </p>
      <ul className="grid grid-cols-2 gap-1.5 rounded-lg bg-surface-2 p-3 font-mono text-sm select-all sm:grid-cols-5" aria-label="Recovery codes">
        {codes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button icon={<Copy className="size-3.5" />} onClick={copy}>
          {copied ? "Copied" : "Copy codes"}
        </Button>
        <Button variant="primary" onClick={onDone}>
          I have saved them
        </Button>
      </div>
    </div>
  );
}

function DisableForm({ busy, onSubmit }: { busy: boolean; onSubmit: (password: string, code: string) => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <Button variant="ghost" onClick={() => setOpen(true)}>
        Turn off two-factor authentication…
      </Button>
    );
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password && code.trim()) onSubmit(password, code.trim());
  };
  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
      <Field label="Password" htmlFor="mfa-off-pw">
        <PasswordInput id="mfa-off-pw" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <Field label="Code (app or recovery code)" htmlFor="mfa-off-code">
        <Input id="mfa-off-code" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} className="font-mono" />
      </Field>
      <Button type="submit" variant="danger" loading={busy}>
        Turn off
      </Button>
    </form>
  );
}

