import { useState, type ReactNode } from "react";
import { NavLink } from "react-router";
import { CalendarClock, LogOut, MailWarning, NotebookPen, Sparkles, UserRound, Watch } from "lucide-react";
import { api } from "../api";
import { useCustomer } from "../pages/my/session";
import { Button, cx } from "./ui";

const NAV = [
  { to: "/my", label: "My watches", icon: Watch, end: true },
  { to: "/my/notes", label: "Notes", icon: NotebookPen, end: false },
  { to: "/my/reminders", label: "Reminders", icon: CalendarClock, end: false },
  { to: "/my/personas", label: "Personas", icon: Sparkles, end: false },
  { to: "/my/account", label: "Account", icon: UserRound, end: false },
];

/**
 * Customer shell, mobile-first: a slim top bar, a bottom tab bar on phones (top links from `sm`).
 * `--bottom-nav` is the height of the bottom bar so sticky footers (the settings save bar) sit above it.
 */
export default function CustomerLayout({ children }: { children: ReactNode }) {
  const { account, signOut } = useCustomer();

  return (
    <div className="min-h-screen pb-[calc(var(--bottom-nav)+env(safe-area-inset-bottom))] [--bottom-nav:4rem] sm:[--bottom-nav:0px]">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-4 sm:px-6">
          <NavLink to="/my" className="flex items-center gap-2">
            <span className="grid size-8 place-items-center rounded-lg bg-accent-bg text-accent">
              <Watch className="size-4" />
            </span>
            <span className="font-semibold tracking-tight">BuddyAI</span>
          </NavLink>
          <nav className="ml-6 hidden items-center gap-1 sm:flex">
            {NAV.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cx(
                    "rounded-lg px-3 py-1.5 text-sm transition",
                    isActive ? "bg-accent-bg font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <span className="hidden max-w-48 truncate text-xs text-muted md:inline">{account.email}</span>
            <button
              onClick={() => void signOut()}
              className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-2 hover:text-fg"
            >
              <LogOut className="size-4" />
              <span>Sign out</span>
            </button>
          </div>
        </div>
      </header>

      {!account.email_verified && <VerifyBanner email={account.email} />}

      <main className="mx-auto w-full max-w-5xl px-4 py-5 sm:px-6 sm:py-8">{children}</main>

      {/* bottom tab bar (phones) */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden"
        aria-label="Main"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cx("flex h-16 flex-col items-center justify-center gap-1 text-[11px]", isActive ? "font-medium text-accent" : "text-muted")
            }
          >
            <Icon className="size-5" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function VerifyBanner({ email }: { email: string }) {
  const { reload } = useCustomer();
  const [state, setState] = useState<"idle" | "sending" | "sent">("idle");
  const [error, setError] = useState<string | null>(null);
  const resend = async () => {
    setState("sending");
    setError(null);
    try {
      await api.me.resendVerification();
      setState("sent");
    } catch (e) {
      setState("idle");
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="border-b border-warn/30 bg-warn-bg">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm text-warn sm:px-6">
        <MailWarning className="size-4 shrink-0" />
        <p className="min-w-0 flex-1 basis-56">
          Please confirm your email address. We sent a link to <b className="break-all">{email}</b>.
          {state === "sent" && " A new link is on its way."}
          {error && <span className="block text-danger">{error}</span>}
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" loading={state === "sending"} disabled={state === "sent"} onClick={resend}>
            {state === "sent" ? "Email sent" : "Resend email"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void reload()}>
            I've confirmed
          </Button>
        </div>
      </div>
    </div>
  );
}
