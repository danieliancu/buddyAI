import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowLeft, MailWarning, Power, Smartphone, Watch } from "lucide-react";
import { api, ApiError } from "../../api";
import { Button, Card, ErrorBox, Field, Input } from "../../components/ui";
import { useCustomer } from "./session";

export default function AddWatchPage() {
  const { account } = useCustomer();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [name, setName] = useState("My BuddyAI");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const verified = account.email_verified;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) return setError(new Error("The code has 6 digits — you'll find it on the watch screen."));
    setBusy(true);
    setError(null);
    try {
      const r = await api.me.devices.pair(code, name.trim() || "My BuddyAI");
      navigate(`/my/watch/${encodeURIComponent(r.device_id)}`, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setError(new Error("Please confirm your email address first."));
      else setError(err);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-lg">
      <Link to="/my" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> My watches
      </Link>
      <h1 className="mb-1 text-xl font-semibold tracking-tight sm:text-2xl">Add a watch</h1>
      <p className="mb-5 text-sm text-muted">Three quick steps. Keep your phone close to the watch.</p>

      <ol className="mb-5 space-y-3">
        <Step n={1} icon={<Power className="size-4" />} title="Turn on the watch">
          Hold the side button until the screen lights up.
        </Step>
        <Step n={2} icon={<Smartphone className="size-4" />} title="Connect it to your Wi-Fi">
          On your phone, open Wi-Fi settings and join the network <b className="font-mono text-fg">BuddyAI-XXXX</b>. A page opens:
          pick your home Wi-Fi and enter its password.
        </Step>
        <Step n={3} icon={<Watch className="size-4" />} title="Enter the code">
          The watch shows a 6-digit code. Type it below — it's valid for 5 minutes.
        </Step>
      </ol>

      {!verified ? (
        <div className="flex items-start gap-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn">
          <MailWarning className="mt-0.5 size-4 shrink-0" />
          <p>
            Before adding a watch, please confirm your email address using the link we sent to <b className="break-all">{account.email}</b>.
            You can resend it from the banner above.
          </p>
        </div>
      ) : (
        <Card>
          <form onSubmit={submit} className="space-y-4">
            <Field label="Code shown on the watch" htmlFor="pair-code">
              <Input
                id="pair-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="h-16 text-center font-mono text-3xl tracking-[0.4em]"
                autoFocus
              />
            </Field>
            <Field label="Watch name" htmlFor="pair-name" hint="For example the name of the person wearing it.">
              <Input id="pair-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <ErrorBox error={error} />
            <Button type="submit" variant="primary" className="h-12 w-full text-base" loading={busy} disabled={code.length !== 6}>
              Add watch
            </Button>
          </form>
        </Card>
      )}
    </div>
  );
}

function Step({ n, icon, title, children }: { n: number; icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3 rounded-xl border border-border bg-surface p-4">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent-bg text-sm font-semibold text-accent">{n}</span>
      <div className="min-w-0">
        <p className="flex items-center gap-2 font-medium">
          {title} <span className="text-muted">{icon}</span>
        </p>
        <p className="mt-0.5 text-sm text-muted">{children}</p>
      </div>
    </li>
  );
}
