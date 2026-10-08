import { Link } from "react-router";
import { useState } from "react";
import { AlertTriangle, ChevronRight, MessageCircleQuestion, MessagesSquare, Plus, Watch } from "lucide-react";
import { api, type Device, type LiveEvent, type MyUsage } from "../../api";
import { useLive } from "../../live";
import { fmtAgo } from "../../format";
import { BatteryInfo, OnlineDot, StateBadge } from "../../components/DeviceBits";
import { buttonCls, Card, Empty, ErrorBox, Spinner, cx, useAsync } from "../../components/ui";
import { fmtDayMonth, turnRefusedText } from "../../components/BillingBits";
import { useCustomer } from "./session";

/** Apply a live event to a device list (status, battery, state). */
function applyEvent(list: Device[], e: LiveEvent): Device[] {
  if (!("device_id" in e)) return list;
  return list.map((d) => {
    if (d.id !== e.device_id) return d;
    switch (e.type) {
      case "device_online":
        return { ...d, online: true, state: "idle", last_seen_at: new Date().toISOString() };
      case "device_offline":
        return { ...d, online: false, state: null, last_seen_at: new Date().toISOString() };
      case "device_state":
        return { ...d, online: true, state: e.state };
      case "device_status":
        return {
          ...d,
          battery_pct: e.battery_pct ?? d.battery_pct,
          charging: e.charging ?? d.charging,
          rssi: e.rssi ?? d.rssi,
        };
      default:
        return d;
    }
  });
}

export default function MyWatchesPage() {
  const { account } = useCustomer();
  const devices = useAsync(api.me.devices.list, []);
  const usage = useAsync(api.me.usage, []);
  const [refused, setRefused] = useState<Record<string, string>>({});

  useLive((e) => {
    if (e.type === "turn_refused") setRefused((m) => ({ ...m, [e.device_id]: e.code }));
    else if (e.type === "turn_end") setRefused((m) => (e.device_id in m ? { ...m, [e.device_id]: "" } : m));
    if (e.type === "device_paired") devices.reload();
    else devices.setData((l) => (l ? applyEvent(l, e) : l));
    if (e.type === "turn_end") usage.reload();
  });

  const list = devices.data ?? [];
  const first = account.name.trim().split(/\s+/)[0];

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-5 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{first ? `Hi, ${first}` : "My watches"}</h1>
          <p className="mt-1 text-sm text-muted">
            {devices.data ? (list.length ? `${list.length} ${list.length === 1 ? "watch" : "watches"}` : "No watches yet") : " "}
          </p>
        </div>
        <AddWatchButton verified={account.email_verified} />
      </div>

      {usage.data && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent-bg text-accent">
            <MessageCircleQuestion className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs text-muted">AI interactions this month · renews {fmtDayMonth(usage.data.reset_at)}</p>
            <UsageBar pct={usagePct(usage.data)} />
          </div>
        </div>
      )}

      {!account.email_verified && (
        <p className="mb-4 rounded-xl border border-border bg-surface px-4 py-3 text-sm text-muted">
          Confirm your email address to add a watch. Use the link we emailed you, or “Resend email” above.
        </p>
      )}

      <ErrorBox error={devices.error} onRetry={devices.reload} />
      {devices.loading && !devices.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={<Watch className="size-8" />} title="Set up your first watch">
            Choose your phone, connect the watch to Wi-Fi and pair it. It takes about five minutes.
            <div className="mt-4">
              <Link to="/my/setup" className={buttonCls("primary")}>
                <Plus className="size-4" /> Set up my watch
              </Link>
            </div>
          </Empty>
        </Card>
      ) : (
        <ul className="space-y-3">
          {list.map((d) => (
            <li key={d.id}>
              <WatchCard device={d} refused={refused[d.id]} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AddWatchButton({ verified }: { verified: boolean }) {
  if (!verified) {
    return (
      <span className={cx(buttonCls("primary"), "pointer-events-none opacity-50")} aria-disabled="true" title="Confirm your email first">
        <Plus className="size-4" /> Add watch
      </span>
    );
  }
  return (
    <Link to="/my/setup?another=1" className={buttonCls("primary")}>
      <Plus className="size-4" /> Add watch
    </Link>
  );
}

function WatchCard({ device: d, refused }: { device: Device; refused?: string }) {
  const href = `/my/watch/${encodeURIComponent(d.id)}`;
  return (
    <div className="rounded-xl border border-border bg-surface">
      <Link to={href} className="flex items-center gap-3 p-4 hover:bg-surface-2/50">
        <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-surface-2 text-accent">
          <Watch className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <OnlineDot online={d.online} />
            <h2 className="truncate font-semibold">{d.name}</h2>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <StateBadge online={d.online} state={d.state} />
            {d.battery_pct != null && <BatteryInfo pct={d.battery_pct} charging={d.charging} />}
            {!d.online && <span className="text-xs">{d.last_seen_at ? `Seen ${fmtAgo(d.last_seen_at)}` : "Not connected yet"}</span>}
          </div>
        </div>
        <ChevronRight className="size-5 shrink-0 text-muted" />
      </Link>
      {refused && (
        <p className="mx-4 mb-3 flex items-start gap-2 rounded-lg bg-warn-bg px-3 py-2 text-xs text-warn">
          <AlertTriangle className="mt-px size-3.5 shrink-0" />
          <span>
            {turnRefusedText(refused)}{" "}
            {refused !== "account_inactive" && (
              <Link to="/my/account" className="underline">
                See plan
              </Link>
            )}
          </span>
        </p>
      )}
      <div className="flex border-t border-border text-sm">
        <Link to={href} className="flex-1 px-4 py-2.5 text-center text-muted hover:text-fg">
          Settings
        </Link>
        <Link to={`${href}/history`} className="flex flex-1 items-center justify-center gap-1.5 border-l border-border px-4 py-2.5 text-muted hover:text-fg">
          <MessagesSquare className="size-4" /> History
        </Link>
      </div>
    </div>
  );
}

/** % of the month's AI interactions used (0-100, floored). */
export function usagePct(u: MyUsage): number {
  if (u.used_pct != null) return Math.max(0, Math.min(100, u.used_pct));
  return u.limit ? Math.max(0, Math.min(100, Math.floor((u.questions * 100) / u.limit))) : 0;
}

/** 0% [bar] 100%: how much of the month's AI interactions is used. */
function UsageBar({ pct }: { pct: number }) {
  return (
    <div className="mt-1.5 flex items-center gap-2 text-xs text-muted" data-testid="usage-bar">
      <span className="tabular">0%</span>
      <div
        className="liquid flex-1"
        style={{ height: 10 }}
        role="progressbar"
        aria-label="Monthly AI interactions used"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        title={`${pct}% used`}
      >
        <div className={cx("liquid-fill", pct >= 100 ? "danger" : pct >= 80 ? "warn" : "")} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular">100%</span>
    </div>
  );
}
