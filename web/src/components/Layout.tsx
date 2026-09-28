import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router";
import { Activity, Cpu, LogOut, Menu, MessagesSquare, Server, Sparkles, Watch, X } from "lucide-react";
import { useLive } from "../live";
import { cx } from "./ui";

const NAV = [
  { to: "/devices", label: "Devices", icon: Watch },
  { to: "/conversations", label: "Conversations", icon: MessagesSquare },
  { to: "/personas", label: "Personas", icon: Sparkles },
  { to: "/usage", label: "Usage & diagnostics", icon: Activity },
  { to: "/firmware", label: "Firmware", icon: Cpu },
  { to: "/system", label: "System", icon: Server },
];

export default function Layout({ user, onLogout, children }: { user: string; onLogout: () => void; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const { connected } = useLive(() => undefined);

  useEffect(() => setOpen(false), [pathname]);

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-4 py-5">
        <div className="grid size-8 place-items-center rounded-lg bg-accent-bg text-accent">
          <Watch className="size-4" />
        </div>
        <span className="font-semibold tracking-tight">BuddyAI</span>
        <span className="ml-auto text-[10px] font-medium tracking-wider text-muted uppercase">Admin</span>
      </div>
      <nav className="flex-1 space-y-0.5 px-2">
        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cx(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
                isActive ? "bg-accent-bg font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="space-y-3 border-t border-border px-4 py-4 text-xs">
        <div className="flex items-center gap-2 text-muted" title="Live connection to the server">
          <span className={cx("size-2 rounded-full", connected ? "bg-ok" : "bg-warn animate-pulse")} />
          {connected ? "Live connected" : "Reconnecting…"}
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-muted">{user}</span>
          <button onClick={onLogout} className="inline-flex items-center gap-1.5 rounded px-2 py-1 text-muted hover:bg-surface-2 hover:text-fg">
            <LogOut className="size-3.5" /> Log out
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen lg:pl-60">
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-border bg-surface lg:block">{sidebar}</aside>

      {/* mobile top bar + drawer */}
      <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-surface/95 px-4 py-3 backdrop-blur lg:hidden">
        <button onClick={() => setOpen(true)} className="rounded p-1 hover:bg-surface-2" aria-label="Menu">
          <Menu className="size-5" />
        </button>
        <span className="font-semibold">BuddyAI</span>
        <span className={cx("ml-auto size-2 rounded-full", connected ? "bg-ok" : "bg-warn")} />
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-border bg-surface shadow-2xl">
            <button onClick={() => setOpen(false)} className="absolute top-4 right-3 rounded p-1 text-muted hover:bg-surface-2" aria-label="Close menu">
              <X className="size-4" />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 lg:py-8">{children}</main>
    </div>
  );
}
