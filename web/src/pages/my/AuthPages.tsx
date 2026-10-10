import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { CheckCircle2, MailCheck, Watch } from "lucide-react";
import { api, ApiError } from "../../api";
import { Button, buttonCls, ErrorBox, Field, Input, Spinner, PasswordInput } from "../../components/ui";
import { CountrySelect } from "./countries";
import { safeNext } from "./session";
import { SITE_URL } from "../../site";

/** Centered card used by all customer sign-in pages (mobile-first). */
function AuthShell({ title, subtitle, children, footer }: { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center px-4 pt-10 pb-6 sm:justify-center sm:pt-6">
      <div className="w-full max-w-sm">
        <a href={SITE_URL} className="mb-6 flex items-center justify-center gap-2.5" title="olácompanion website">
          <span className="grid size-10 place-items-center rounded-xl bg-accent-bg text-accent">
            <Watch className="size-5" />
          </span>
          <span className="text-lg font-semibold tracking-tight">olá</span>
        </a>
        <div className="rounded-2xl border border-border bg-surface p-5 shadow-xl sm:p-6">
          <h1 className="text-lg font-semibold">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
          <div className="mt-5">{children}</div>
        </div>
        {footer && <div className="mt-5 text-center text-sm text-muted">{footer}</div>}
      </div>
    </div>
  );
}

// ---------- /login ----------

export function LoginPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"), "/my");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState<unknown>(null);

  // Already signed in → straight to the watches.
  useEffect(() => {
    let alive = true;
    api.me
      .get({ no401: true })
      .then(() => alive && navigate(next, { replace: true }))
      .catch(() => alive && setChecking(false));
    return () => {
      alive = false;
    };
  }, [navigate, next]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) return setError(new Error("Enter your email and password."));
    setBusy(true);
    try {
      await api.me.login(email.trim(), password, remember);
      navigate(next, { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setError(new Error("Wrong email or password."));
      else setError(err);
      setBusy(false);
    }
  };

  if (checking) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner />
      </div>
    );
  }

  return (
    <AuthShell
      title="Sign in"
      subtitle={params.get("deleted") ? "Your account was deleted. Thank you for using olá." : "Welcome back to olá."}
      footer={
        <div className="space-y-4">
          <p>
            New here?{" "}
            <Link to="/signup" className="font-medium text-accent hover:underline">
              Create an account
            </Link>
          </p>
          <p className="text-xs">
            <Link to="/admin/login" className="hover:text-fg hover:underline">
              Operator sign-in
            </Link>
          </p>
        </div>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email" htmlFor="li-email">
          <Input id="li-email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </Field>
        <Field label="Password" htmlFor="li-pw">
          <PasswordInput id="li-pw" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="-mt-1 flex items-center justify-between gap-3 text-sm">
          <label className="inline-flex cursor-pointer items-center gap-2 select-none">
            <input
              type="checkbox"
              className="size-4 cursor-pointer accent-accent"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember me
          </label>
          <Link to="/forgot-password" className="text-muted hover:text-fg hover:underline">
            Forgot password?
          </Link>
        </div>
        <ErrorBox error={error} />
        <Button type="submit" variant="primary" className="h-11 w-full" loading={busy}>
          Sign in
        </Button>
      </form>
    </AuthShell>
  );
}

// ---------- /signup ----------

export function SignupPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [country, setCountry] = useState("GB");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) return setError(new Error("Enter a valid email address."));
    if (password.length < 8) return setError(new Error("The password must be at least 8 characters."));
    setBusy(true);
    try {
      await api.me.signup({ email: email.trim(), password, name: name.trim(), country: country || null });
      navigate("/my", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409)
        setError(new Error("An account with this email already exists. Sign in or reset your password."));
      else setError(err);
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title="Create your account"
      subtitle="Manage your watch, its voice and language, and your conversation history."
      footer={
        <p>
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-accent hover:underline">
            Sign in
          </Link>
        </p>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Your name" htmlFor="su-name">
          <Input id="su-name" autoComplete="name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="Email" htmlFor="su-email" hint="We'll send you a link to confirm it.">
          <Input id="su-email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" htmlFor="su-pw" hint="At least 8 characters">
          <PasswordInput id="su-pw" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Country" htmlFor="su-country">
          <CountrySelect id="su-country" value={country} onChange={setCountry} />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" variant="primary" className="h-11 w-full" loading={busy}>
          Create account
        </Button>
      </form>
    </AuthShell>
  );
}

// ---------- /forgot-password ----------

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return setError(new Error("Enter your email address."));
    setBusy(true);
    setError(null);
    try {
      await api.me.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title="Reset your password"
      subtitle={sent ? undefined : "Enter the email you signed up with and we'll send you a link."}
      footer={
        <Link to="/login" className="font-medium text-accent hover:underline">
          Back to sign in
        </Link>
      }
    >
      {sent ? (
        <div className="flex gap-3 text-sm">
          <MailCheck className="size-5 shrink-0 text-ok" />
          <p>
            If an account exists for <b className="break-all">{email.trim()}</b>, a reset link is on its way. It is valid for 1 hour. Check your spam
            folder too.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <Field label="Email" htmlFor="fp-email">
            <Input id="fp-email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" variant="primary" className="h-11 w-full" loading={busy}>
            Send reset link
          </Button>
        </form>
      )}
    </AuthShell>
  );
}

// ---------- /reset-password?token= (also "set your password" for accounts created by the operator) ----------

export function ResetPasswordPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  // The welcome email after a purchase: first password, then straight on to the watch setup.
  const welcome = params.get("welcome") === "1";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (password.length < 8) return setError(new Error("The password must be at least 8 characters."));
    if (password !== confirm) return setError(new Error("The passwords don't match."));
    setBusy(true);
    try {
      await api.me.resetPassword(token, password);
      navigate(welcome ? "/my/setup" : "/my", { replace: true });
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  if (!token) {
    return (
      <AuthShell title="Link incomplete" footer={<Link to="/forgot-password" className="font-medium text-accent hover:underline">Request a new link</Link>}>
        <p className="text-sm text-muted">This link is missing its code. Open the link from the email again, or request a new one.</p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={welcome ? "Set your password" : "Choose a password"}
      subtitle={
        welcome
          ? "Welcome to olá! Choose a password for your account; next you'll set up your watch."
          : "Set a new password for your olá account. You'll be signed in right after."
      }
      footer={
        <Link to="/forgot-password" className="hover:text-fg hover:underline">
          Link expired? Send a new one
        </Link>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <input type="text" autoComplete="username" hidden readOnly />
        <Field label="New password" htmlFor="rp-pw" hint="At least 8 characters">
          <PasswordInput id="rp-pw" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
        </Field>
        <Field label="Repeat password" htmlFor="rp-pw2">
          <PasswordInput id="rp-pw2" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        <Button type="submit" variant="primary" className="h-11 w-full" loading={busy}>
          Save password
        </Button>
      </form>
    </AuthShell>
  );
}

// ---------- /verify-email?token= ----------

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [state, setState] = useState<"working" | "ok" | "error">(token ? "working" : "error");
  const [error, setError] = useState<unknown>(token ? null : new Error("This link is missing its code."));
  const started = useRef(false);

  useEffect(() => {
    // Tokens are single-use: guard against the double effect of React StrictMode.
    if (!token || started.current) return;
    started.current = true;
    api.me
      .verifyEmail(token)
      .then(() => setState("ok"))
      .catch((e) => {
        setError(e);
        setState("error");
      });
  }, [token]);

  if (state === "working") {
    return (
      <AuthShell title="Confirming your email…">
        <Spinner label="One moment…" />
      </AuthShell>
    );
  }
  if (state === "ok") {
    return (
      <AuthShell title="Email confirmed">
        <div className="space-y-5">
          <div className="flex gap-3 text-sm">
            <CheckCircle2 className="size-5 shrink-0 text-ok" />
            <p>Thanks! Your email address is confirmed. You can now add your watch.</p>
          </div>
          <Link to="/my" className={buttonCls("primary", "md", "h-11 w-full")}>
            Continue
          </Link>
        </div>
      </AuthShell>
    );
  }
  return (
    <AuthShell
      title="We couldn't confirm your email"
      footer={
        <Link to="/my" className="font-medium text-accent hover:underline">
          Go to my account
        </Link>
      }
    >
      <div className="space-y-3 text-sm">
        <ErrorBox error={error} />
        <p className="text-muted">
          Links expire after 48 hours and work only once. Sign in and use “Resend email” to get a new link. If you already confirmed, you
          don't need to do anything.
        </p>
      </div>
    </AuthShell>
  );
}
