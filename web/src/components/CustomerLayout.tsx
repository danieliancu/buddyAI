import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router";
import { Brain, CalendarClock, MailWarning, NotebookPen, Sparkles, UserRound, Watch } from "lucide-react";
import { api } from "../api";
import { useCustomer } from "../pages/my/session";
import { Button, cx } from "./ui";
import BrandMark from "./BrandMark";
import UsageNotice from "./UsageNotice";
import { SITE_URL } from "../site";

const NAV = [
  { to: "/my", label: "My watches", icon: Watch, end: true },
  { to: "/my/notes", label: "Notes", icon: NotebookPen, end: false },
  { to: "/my/reminders", label: "Reminders", icon: CalendarClock, end: false },
  { to: "/my/personas", label: "Personas", icon: Sparkles, end: false },
  { to: "/my/account", label: "Account", icon: UserRound, end: false },
];
// Desktop only (the phone tab bar keeps five tabs; on phones Memory is reached from Account).
const MEMORY = { to: "/my/memory", label: "Memory", icon: Brain, end: false };

/**
 * Customer shell, mobile-first: a light header (logo, account button), a floating bottom tab bar on phones
 * (top links from `sm`), soft blue waves behind the content (.app-backdrop).
 * `--bottom-nav` is the height of the bottom bar so sticky footers (the settings save bar) sit above it.
 */
export default function CustomerLayout({ children }: { children: ReactNode }) {
  const { account } = useCustomer();
  // The header is see-through over the background waves; it gets a frosted background once the page scrolls.
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 4);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);

  return (
    <div className="app-backdrop min-h-screen pb-[calc(var(--bottom-nav)+env(safe-area-inset-bottom))] [--bottom-nav:5rem] sm:[--bottom-nav:0px]">
      <header className={cx("sticky top-0 z-30 transition-colors", scrolled && "bg-bg/80 shadow-[0_1px_0_var(--border)] backdrop-blur-md")}>
        <div className="mx-auto flex h-20 max-w-5xl items-center gap-3 px-4 sm:px-6">
          <a href={SITE_URL} className="flex items-center gap-3" title="olácompanion website" aria-label="olá — go to the website">
            <BrandMark />
            <span className="text-[1.75rem] font-semibold tracking-tight">olá</span>
          </a>
          <nav className="ml-6 hidden items-center gap-1 sm:flex">
            {(account.memory ? [...NAV.slice(0, 4), MEMORY, NAV[4]] : NAV).map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cx(
                    "rounded-full px-3.5 py-2 text-sm transition",
                    isActive ? "bg-accent-bg font-semibold text-accent" : "text-muted hover:bg-surface hover:text-fg",
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <NavLink
            to="/my/account"
            aria-label="Account"
            title={account.email}
            className="ml-auto grid size-12 shrink-0 place-items-center rounded-full bg-surface text-fg shadow-(--card-shadow) transition hover:text-accent"
          >
            <UserRound className="size-6" />
          </NavLink>
        </div>
      </header>

      {!account.email_verified && <VerifyBanner email={account.email} />}
      <UsageNotice />

      <main className="mx-auto w-full max-w-5xl px-4 py-4 sm:px-6 sm:py-8">{children}</main>

      {/* bottom tab bar (phones) */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 rounded-t-[28px] bg-(--tabbar) px-2 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] shadow-[0_-10px_30px_-14px_rgba(31,80,190,0.25)] backdrop-blur sm:hidden"
        aria-label="Main"
      >
        <div className="grid grid-cols-5 gap-1">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cx(
                  "flex h-16 flex-col items-center justify-center gap-1 rounded-2xl text-[11px] transition",
                  isActive ? "bg-accent-bg font-semibold text-accent" : "text-muted",
                )
              }
            >
              <Icon className="size-6" />
              {label}
            </NavLink>
          ))}
        </div>
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
    <div className="mx-auto max-w-5xl px-4 pt-3 sm:px-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn">
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
