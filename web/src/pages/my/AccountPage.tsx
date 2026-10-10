import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { AlertTriangle, BadgeCheck, Brain, ChevronRight, Download, KeyRound, LogOut, Monitor, Moon, Palette, Sun, Trash2, UserRound } from "lucide-react";
import { api, ApiError } from "../../api";
import { fmtDate } from "../../format";
import { Badge, Button, buttonCls, Card, Dialog, ErrorBox, Field, Input, PasswordInput } from "../../components/ui";
import { CountrySelect } from "./countries";
import PlanCard from "./PlanCard";
import { useCustomer } from "./session";
import { useThemePref, type ThemePref } from "../../theme";

export default function AccountPage() {
  const { account, signOut } = useCustomer();
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Account</h1>
        <p className="mt-1 text-sm text-muted">Member since {fmtDate(account.created_at)}</p>
      </div>
      <PlanCard />
      {account.memory && (
        <Link to="/my/memory" className="block">
          <Card title={<Title icon={<Brain className="size-4" />}>Memory</Title>} actions={<ChevronRight className="size-4 text-muted" />}>
            <p className="text-sm text-muted">What olá remembers about you: see, correct or forget it.</p>
          </Card>
        </Link>
      )}
      <ProfileCard />
      <PasswordCard />
      <AppearanceCard />
      <Card title="Your data">
        <p className="mb-3 text-sm text-muted">
          Download everything we store about you: your profile, watch settings, conversation history, personas, notes and reminders, and memories (JSON file).
        </p>
        <a href={api.me.exportUrl} download="ola-my-data.json" className={buttonCls("secondary")}>
          <Download className="size-4" /> Download my data
        </a>
      </Card>
      <DeleteAccountCard />
      <div className="sm:hidden">
        <Button className="w-full" variant="ghost" icon={<LogOut className="size-4" />} onClick={() => void signOut()}>
          Sign out
        </Button>
      </div>
    </div>
  );
}

function ProfileCard() {
  const { account, setAccount } = useCustomer();
  const [name, setName] = useState(account.name);
  const [country, setCountry] = useState(account.country ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const dirty = name.trim() !== account.name || country !== (account.country ?? "");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const a = await api.me.updateProfile({ name: name.trim(), country });
      setAccount(a);
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={<Title icon={<UserRound className="size-4" />}>Profile</Title>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email">
          <div className="flex h-10 items-center gap-2 text-sm">
            <span className="min-w-0 truncate">{account.email}</span>
            {account.email_verified ? (
              <Badge tone="ok">
                <BadgeCheck className="size-3" /> Confirmed
              </Badge>
            ) : (
              <Badge tone="warn">Not confirmed</Badge>
            )}
          </div>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" htmlFor="acc-name">
            <Input id="acc-name" maxLength={120} autoComplete="name" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} />
          </Field>
          <Field label="Country" htmlFor="acc-country">
            <CountrySelect id="acc-country" value={country} onChange={(c) => { setCountry(c); setSaved(false); }} />
          </Field>
        </div>
        <ErrorBox error={error} />
        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" loading={busy} disabled={!dirty}>
            Save
          </Button>
          {saved && !dirty && <span className="text-sm text-ok">Saved</span>}
        </div>
      </form>
    </Card>
  );
}

const THEMES: { value: ThemePref; label: string; icon: ReactNode }[] = [
  { value: "light", label: "Light", icon: <Sun className="size-4" /> },
  { value: "dark", label: "Dark", icon: <Moon className="size-4" /> },
  { value: "system", label: "System", icon: <Monitor className="size-4" /> },
];

function AppearanceCard() {
  const [pref, setPref] = useThemePref();
  return (
    <Card title={<Title icon={<Palette className="size-4" />}>Appearance</Title>}>
      <p className="mb-3 text-sm text-muted">System follows your phone or computer. Saved in this browser.</p>
      <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-1 rounded-lg border border-border bg-surface-2 p-1">
        {THEMES.map((t) => (
          <button
            key={t.value}
            type="button"
            role="radio"
            aria-checked={pref === t.value}
            onClick={() => setPref(t.value)}
            className={
              pref === t.value
                ? "flex items-center justify-center gap-2 rounded-md bg-surface px-3 py-2 text-sm font-medium text-fg shadow-sm"
                : "flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm text-muted transition hover:text-fg"
            }
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next.length < 8) return setError(new Error("The new password must be at least 8 characters."));
    if (next !== confirm) return setError(new Error("The new passwords don't match."));
    setBusy(true);
    try {
      await api.me.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      // 401 here means a wrong current password (the session stays valid).
      if (err instanceof ApiError && err.status === 401) setError(new Error("The current password is wrong."));
      else setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={<Title icon={<KeyRound className="size-4" />}>Password</Title>}>
      <form onSubmit={submit} className="space-y-4">
        <input type="text" autoComplete="username" hidden readOnly />
        <Field label="Current password" htmlFor="pw-cur">
          <PasswordInput id="pw-cur" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="New password" htmlFor="pw-new" hint="At least 8 characters">
            <PasswordInput id="pw-new" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Field label="Repeat new password" htmlFor="pw-new2">
            <PasswordInput id="pw-new2" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
        </div>
        <ErrorBox error={error} />
        {done && <p className="text-sm text-ok">Password changed. Other devices were signed out.</p>}
        <Button type="submit" loading={busy} disabled={!current || !next}>
          Change password
        </Button>
      </form>
    </Card>
  );
}

function DeleteAccountCard() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const close = () => {
    setOpen(false);
    setPassword("");
    setError(null);
  };
  const run = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!password) return setError(new Error("Enter your password to confirm."));
    setBusy(true);
    setError(null);
    try {
      await api.me.deleteAccount(password);
      navigate("/login?deleted=1", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 403 ? new Error("Wrong password.") : err);
      setBusy(false);
    }
  };

  return (
    <Card className="border-danger/40" title={<Title icon={<AlertTriangle className="size-4 text-danger" />}>Delete account</Title>}>
      <p className="mb-3 text-sm text-muted">
        Permanently delete your account and everything in it. Download your data first if you want to keep a copy.
      </p>
      <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={() => setOpen(true)}>
        Delete account…
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Delete your account?"
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="danger" type="submit" form="del-acc" loading={busy} disabled={!password}>
              Delete permanently
            </Button>
          </>
        }
      >
        <form id="del-acc" onSubmit={run} className="space-y-4 text-sm">
          <div className="rounded-lg border border-danger/30 bg-danger-bg px-3 py-3 text-danger">
            <p className="font-medium">This cannot be undone. We will:</p>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-5">
              <li>erase your profile, personas, memories and all conversation history;</li>
              <li>remove your watches from the account (they go back to the pairing screen);</li>
              <li>sign you out everywhere.</li>
            </ul>
          </div>
          <input type="text" autoComplete="username" hidden readOnly />
          <Field label="Enter your password to confirm" htmlFor="del-pw">
            <PasswordInput id="del-pw" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <ErrorBox error={error} />
        </form>
      </Dialog>
    </Card>
  );
}

function Title({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-accent">{icon}</span>
      {children}
    </span>
  );
}
