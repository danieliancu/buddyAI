import { Battery, BatteryCharging, BatteryLow, BatteryMedium, BatteryFull, Wifi, WifiLow, WifiOff } from "lucide-react";
import type { Device, TurnStatus } from "../api";
import { STATE_LABEL, STATUS_LABEL } from "../format";
import { Badge, Select, cx } from "./ui";

export function OnlineDot({ online }: { online: boolean }) {
  return (
    <span className="relative inline-flex size-2.5" title={online ? "Online" : "Offline"}>
      {online && <span className="absolute inset-0 animate-ping rounded-full bg-ok opacity-50" />}
      <span className={cx("relative inline-flex size-2.5 rounded-full", online ? "bg-ok" : "bg-muted/50")} />
    </span>
  );
}

export function StateBadge({ online, state }: { online: boolean; state: string | null }) {
  if (!online) return <Badge>Offline</Badge>;
  const s = state ?? "idle";
  const tone = s === "listening" ? "accent" : s === "thinking" ? "warn" : s === "speaking" ? "ok" : "neutral";
  return (
    <Badge tone={tone}>
      {s !== "idle" && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {STATE_LABEL[s] ?? s}
    </Badge>
  );
}

export function BatteryInfo({ pct, charging }: { pct: number | null; charging: boolean | null }) {
  if (pct == null) return <span className="text-muted">—</span>;
  const Icon = charging ? BatteryCharging : pct >= 80 ? BatteryFull : pct >= 40 ? BatteryMedium : pct >= 15 ? Battery : BatteryLow;
  return (
    <span className={cx("inline-flex items-center gap-1 tabular", pct < 15 && !charging ? "text-danger" : "")}>
      <Icon className="size-4" />
      {pct}%
    </span>
  );
}

export function RssiInfo({ rssi }: { rssi: number | null }) {
  if (rssi == null) return <span className="text-muted">—</span>;
  const Icon = rssi >= -60 ? Wifi : rssi >= -75 ? WifiLow : WifiOff;
  return (
    <span className="inline-flex items-center gap-1 tabular" title={`${rssi} dBm`}>
      <Icon className="size-4" />
      {rssi} dBm
    </span>
  );
}

export function TurnStatusBadge({ status }: { status: TurnStatus }) {
  if (status === "completed") return null;
  const tone = status === "error" ? "danger" : status === "aborted" ? "warn" : "neutral";
  return <Badge tone={tone}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function DevicePicker({
  devices,
  value,
  onChange,
  allowAll,
}: {
  devices: Device[];
  value: string;
  onChange: (id: string) => void;
  allowAll?: boolean;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className="w-auto min-w-44" aria-label="Device">
      {allowAll && <option value="">All watches</option>}
      {!allowAll && !value && <option value="">Choose a watch…</option>}
      {devices.map((d) => (
        <option key={d.id} value={d.id}>
          {d.name}
          {d.online ? " • online" : ""}
        </option>
      ))}
    </Select>
  );
}
