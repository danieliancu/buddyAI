import { useState, type ReactNode } from "react";
import { NavLink } from "react-router";
import { Brain, CalendarClock, MailWarning, NotebookPen, Sparkles, UserRound, Watch } from "lucide-react";
import { api } from "../api";
import { useCustomer } from "../pages/my/session";
import { Button, cx } from "./ui";
import { SiteLogo } from "./BrandMark";
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

/** The name the website's header shows for a signed-in visitor (server/app/api/me.py `badge`). */
export function headerName(account: { name: string; email: string }): string {
  const parts = (account.name || "").split(/\s+/).filter(Boolean);
  const first = (parts[0] ?? account.email.split("@")[0]).slice(0, 24);
  return `${first} ${parts.slice(1).join(" ").slice(0, 32)}`.trim();
}

/**
 * Customer shell, mobile-first. The top bar is the website's header (site/src/components/Header.astro) with the
 * account's links: same height, width, logo, link sizes and the signed-in green lock + name, so moving between
 * the site and the account the header does not change. Links from `lg` as on the site; below that a floating
 * bottom tab bar. Soft blue waves behind the content (.app-backdrop).
 * `--bottom-nav` is the height of the bottom bar so sticky footers (the settings save bar) sit above it.
 */
export default function CustomerLayout({ children }: { children: ReactNode }) {
  const { account } = useCustomer();
  const name = headerName(account);

  return (
    <div className="app-backdrop min-h-screen pb-[calc(var(--bottom-nav)+env(safe-area-inset-bottom))] [--bottom-nav:5rem] lg:[--bottom-nav:0px]">
      <header className="site-header sticky top-0 z-40 border-b border-(--sh-line) bg-(--sh-bg) backdrop-blur-md">
        <div className="mx-auto flex h-16 w-full max-w-[76rem] items-center justify-between gap-4 px-4 sm:px-6">
          <a href={SITE_URL} className="flex items-center gap-2 rounded-lg" aria-label="olá home" title="olácompanion website">
            <SiteLogo />
          </a>
          <nav aria-label="Account" className="hidden lg:block">
            <ul className="flex items-center gap-2">
              {(account.memory ? [...NAV.slice(0, 4), MEMORY, NAV[4]] : NAV).map(({ to, label, end }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={end}
                    className={({ isActive }) =>
                      cx(
                        "rounded-lg px-3 py-2 text-sm font-medium transition-colors hover:text-(--sh-accent)",
                        isActive ? "text-(--sh-accent)" : "text-(--sh-fg)",
                      )
                    }
                  >
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="flex items-center gap-3">
            <NavLink
              to="/my/account"
              className="inline-flex max-w-56 items-center gap-1.5 rounded-lg px-1.5 py-1 text-(--sh-fg) hover:bg-(--sh-hover)"
              title={`Your olá account (signed in)`}
            >
              <svg
                width={20}
                height={20}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="shrink-0 text-(--sh-ok)"
                aria-hidden="true"
                focusable="false"
              >
                <rect x="5" y="11" width="14" height="10" rx="2" />
                <path d="M8 11V8a4 4 0 0 1 8 0v3" />
              </svg>
              <span className="hidden truncate text-sm font-medium sm:inline">{name}</span>
              <span className="sr-only">, signed in</span>
            </NavLink>
          </div>
        </div>
      </header>

      {!account.email_verified && <VerifyBanner email={account.email} />}

      <main className="mx-auto w-full max-w-5xl px-4 py-4 sm:px-6 sm:py-8">{children}</main>

      {/* bottom tab bar (phones) */}
      <nav
        className="fixed inset-x-0 bottom-0 z-30 rounded-t-[28px] bg-(--tabbar) px-2 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] shadow-[0_-10px_30px_-14px_rgba(31,80,190,0.25)] backdrop-blur lg:hidden"
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
