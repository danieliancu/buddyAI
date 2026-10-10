import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router";
import {
  Activity,
  ChevronRight,
  Cpu,
  LogOut,
  Menu,
  MessagesSquare,
  Package,
  Server,
  Sparkles,
  Stethoscope,
  Users,
  Watch,
  X,
  type LucideIcon,
} from "lucide-react";
import { useLive } from "../live";
import { cx } from "./ui";

type NavItem = { to: string; label: string; icon: LucideIcon };

/** The operator menu, in groups (light sidebar, white content panel - see .admin-ui in index.css). */
const NAV: { title: string; items: NavItem[] }[] = [
  {
    title: "General",
    items: [
      { to: "/admin/devices", label: "Devices", icon: Watch },
      { to: "/admin/customers", label: "Customers", icon: Users },
      { to: "/admin/orders", label: "Orders", icon: Package },
    ],
  },
  {
    title: "Activity",
    items: [
      { to: "/admin/conversations", label: "Conversations", icon: MessagesSquare },
      { to: "/admin/usage", label: "Usage & performance", icon: Activity },
      { to: "/admin/diagnostics", label: "olá Diagnostics", icon: Stethoscope },
    ],
  },
  {
    title: "Configuration",
    items: [
      { to: "/admin/personas", label: "Personas", icon: Sparkles },
      { to: "/admin/firmware", label: "Firmware", icon: Cpu },
      { to: "/admin/system", label: "System", icon: Server },
    ],
  },
];

const ALL = NAV.flatMap((g) => g.items);

export default function Layout({ user, onLogout, children }: { user: string; onLogout: () => void; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const { connected } = useLive(() => undefined);
  const current = ALL.find((n) => pathname === n.to || pathname.startsWith(n.to + "/"));

  useEffect(() => setOpen(false), [pathname]);

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2.5 px-5 pt-6 pb-5">
        <div className="grid size-8 place-items-center rounded-lg bg-fg text-surface">
          <Watch className="size-4" />
        </div>
        <span className="text-[15px] font-semibold tracking-tight">olá</span>
        <span className="ml-auto rounded-md border border-border bg-surface px-1.5 py-0.5 text-[10px] font-medium tracking-wider text-muted uppercase">
          Admin
        </span>
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 pb-4">
        {NAV.map((group) => (
          <div key={group.title}>
            <p className="px-3 pb-1.5 text-[13px] text-muted">{group.title}</p>
            <div className="space-y-0.5">
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    cx(
                      "flex h-9 items-center gap-3 rounded-lg px-3 text-[15px] transition",
                      isActive ? "bg-black/[0.06] font-medium text-fg" : "text-fg/80 hover:bg-black/[0.04] hover:text-fg",
                    )
                  }
                >
                  <Icon className="size-[18px] shrink-0" strokeWidth={1.75} />
                  {label}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>
      <div className="space-y-3 border-t border-border px-5 py-4 text-xs">
        <div className="flex items-center gap-2 text-muted" title="Live connection to the server">
          <span className={cx("size-2 rounded-full", connected ? "bg-ok" : "bg-warn animate-pulse")} />
          {connected ? "Live connected" : "Reconnecting…"}
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-muted">{user}</span>
          <button onClick={onLogout} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-muted hover:bg-black/[0.05] hover:text-fg">
            <LogOut className="size-3.5" /> Log out
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="admin-ui min-h-screen bg-bg text-fg lg:pl-64">
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-64 bg-bg lg:block">{sidebar}</aside>

      {/* mobile top bar + drawer */}
      <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-surface/95 px-4 py-3 backdrop-blur lg:hidden">
        <button onClick={() => setOpen(true)} className="rounded p-1 hover:bg-surface-2" aria-label="Menu">
          <Menu className="size-5" />
        </button>
        <span className="font-semibold">olá</span>
        <span className={cx("ml-auto size-2 rounded-full", connected ? "bg-ok" : "bg-warn")} />
      </div>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 border-r border-border bg-bg shadow-2xl">
            <button onClick={() => setOpen(false)} className="absolute top-5 right-3 rounded p-1 text-muted hover:bg-surface-2" aria-label="Close menu">
              <X className="size-4" />
            </button>
            {sidebar}
          </aside>
        </div>
      )}

      {/* content: a white panel with a breadcrumb bar */}
      <div className="lg:py-3 lg:pr-3">
        <div className="min-h-screen bg-surface lg:min-h-[calc(100vh-1.5rem)] lg:rounded-2xl lg:border lg:border-border lg:shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
          <div className="hidden h-14 items-center gap-2 border-b border-border px-8 text-[15px] lg:flex">
            <span className="text-muted">Admin</span>
            {current && (
              <>
                <ChevronRight className="size-3.5 text-muted/60" />
                <current.icon className="size-4" strokeWidth={1.75} />
                <span>{current.label}</span>
              </>
            )}
          </div>
          <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-8 lg:py-9">{children}</main>
        </div>
      </div>
    </div>
  );
}
