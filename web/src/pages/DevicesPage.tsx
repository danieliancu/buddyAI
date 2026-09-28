import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { Check, Copy, MessagesSquare, Pencil, Plus, SlidersHorizontal, Trash2, Watch } from "lucide-react";
import { api, ApiError, type Device, type LiveEvent, type PendingPairing } from "../api";
import { useLive } from "../live";
import { fmtAgo, fmtDateTime } from "../format";
import { BatteryInfo, OnlineDot, RssiInfo, StateBadge } from "../components/DeviceBits";
import { Badge, Button, buttonCls, Card, ConfirmDialog, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, useAsync } from "../components/ui";

interface LastTurn {
  user_text: string;
  assistant_text: string;
  status: string;
}

export default function DevicesPage() {
  const devices = useAsync(api.devices.list, []);
  const info = useAsync(api.system.info, []);
  const [pending, setPending] = useState<PendingPairing[]>([]);
  const [lastTurn, setLastTurn] = useState<Record<string, LastTurn>>({});
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Device | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);
  const [, setNow] = useState(Date.now());

  const loadPending = useCallback(() => {
    api.devices.pending().then(setPending).catch(() => undefined);
  }, []);

  useEffect(() => {
    loadPending();
    const t = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(t);
  }, [loadPending]);

  const update = (id: string, patch: Partial<Device>) =>
    devices.setData((list) => list?.map((d) => (d.id === id ? { ...d, ...patch } : d)) ?? list);

  useLive((e: LiveEvent) => {
    switch (e.type) {
      case "device_online":
        update(e.device_id, { online: true, state: "idle", last_seen_at: new Date().toISOString() });
        break;
      case "device_offline":
        update(e.device_id, { online: false, state: null, last_seen_at: new Date().toISOString() });
        loadPending(); // pending requests of a disconnected watch are dropped by the server
        break;
      case "device_state":
        update(e.device_id, { state: e.state, online: true });
        break;
      case "device_status": {
        const p: Partial<Device> = {};
        if (e.battery_pct != null) p.battery_pct = e.battery_pct;
        if (e.charging != null) p.charging = e.charging;
        if (e.rssi != null) p.rssi = e.rssi;
        update(e.device_id, p);
        break;
      }
      case "turn_end":
        setLastTurn((m) => ({ ...m, [e.device_id]: e }));
        break;
      case "pairing_pending":
        loadPending();
        break;
      case "device_paired":
        loadPending();
        devices.reload();
        break;
    }
  });

  const list = devices.data ?? [];
  const onlineCount = list.filter((d) => d.online).length;

  return (
    <>
      <PageHeader
        title="Devices"
        subtitle={devices.data ? `${list.length} ${list.length === 1 ? "watch" : "watches"} • ${onlineCount} online` : undefined}
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>
            Add watch
            {pending.length > 0 && <Badge tone="warn" className="ml-1 border-0">{pending.length}</Badge>}
          </Button>
        }
      />

      {pending.length > 0 && !adding && (
        <button
          onClick={() => setAdding(true)}
          className="mb-4 flex w-full items-center gap-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-left text-sm text-warn"
        >
          <Watch className="size-4 shrink-0" />
          <span className="flex-1">
            {pending.length === 1 ? "A watch is" : `${pending.length} watches are`} waiting to be paired. Enter the code shown on its screen.
          </span>
          <span className="font-medium underline">Add</span>
        </button>
      )}

      <ServerUrlBox url={info.data?.device_ws_url} />

      <ErrorBox error={devices.error} onRetry={devices.reload} />
      {devices.loading && !devices.data ? (
        <Spinner />
      ) : list.length === 0 ? (
        <Card>
          <Empty icon={<Watch className="size-8" />} title="No paired watches">
            Turn the watch on, connect it to Wi-Fi and enter the server address above. Then click “Add watch” and type the 6-digit code
            shown on its screen.
          </Empty>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {list.map((d) => (
            <DeviceCard
              key={d.id}
              device={d}
              lastTurn={lastTurn[d.id]}
              onRename={() => setRenaming(d)}
              onRevoke={() => setRevoking(d)}
            />
          ))}
        </div>
      )}

      <AddWatchDialog
        open={adding}
        pending={pending}
        onRefresh={loadPending}
        onClose={() => setAdding(false)}
        onPaired={() => {
          devices.reload();
          loadPending();
        }}
      />
      <RenameDialog
        device={renaming}
        onClose={() => setRenaming(null)}
        onSaved={(d) => update(d.id, { name: d.name })}
      />
      <ConfirmDialog
        open={!!revoking}
        danger
        title="Revoke watch"
        confirmLabel="Revoke"
        message={
          <>
            <b>{revoking?.name}</b> will be disconnected and return to the pairing screen. To use it again it must be paired with a new
            code.
          </>
        }
        onConfirm={async () => {
          if (!revoking) return;
          await api.devices.revoke(revoking.id);
          devices.setData((l) => l?.filter((x) => x.id !== revoking.id) ?? l);
        }}
        onClose={() => setRevoking(null)}
      />
    </>
  );
}

function DeviceCard({
  device: d,
  lastTurn,
  onRename,
  onRevoke,
}: {
  device: Device;
  lastTurn?: LastTurn;
  onRename: () => void;
  onRevoke: () => void;
}) {
  return (
    <div className="flex flex-col rounded-xl border border-border bg-surface">
      <div className="flex items-start gap-3 p-4">
        <div className="mt-1.5">
          <OnlineDot online={d.online} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold">{d.name}</h3>
            <StateBadge online={d.online} state={d.state} />
          </div>
          <p className="truncate font-mono text-xs text-muted" title={d.id}>
            {d.id}
          </p>
        </div>
        <button onClick={onRename} className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-fg" title="Rename" aria-label="Rename">
          <Pencil className="size-4" />
        </button>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 px-4 pb-4 text-sm sm:grid-cols-4">
        <Info label="Battery">
          <BatteryInfo pct={d.battery_pct} charging={d.charging} />
        </Info>
        <Info label="Signal">
          <RssiInfo rssi={d.rssi} />
        </Info>
        <Info label="Firmware">
          <span className="font-mono text-xs">{d.fw_version || "—"}</span>
        </Info>
        <Info label="Last seen">
          <span title={fmtDateTime(d.last_seen_at)}>{d.online ? "now" : fmtAgo(d.last_seen_at)}</span>
        </Info>
      </dl>
      {lastTurn && (
        <div className="mx-4 mb-4 space-y-1 rounded-lg bg-surface-2 px-3 py-2 text-xs">
          {lastTurn.user_text && (
            <p className="truncate">
              <span className="text-muted">User: </span>
              {lastTurn.user_text}
            </p>
          )}
          {lastTurn.assistant_text && (
            <p className="truncate">
              <span className="text-accent">Buddy: </span>
              {lastTurn.assistant_text}
            </p>
          )}
        </div>
      )}
      <div className="mt-auto flex flex-wrap gap-2 border-t border-border px-4 py-3">
        <Link to={`/devices/${encodeURIComponent(d.id)}`} className={buttonCls("secondary", "sm")}>
          <SlidersHorizontal className="size-3.5" /> Settings
        </Link>
        <Link to={`/conversations?device=${encodeURIComponent(d.id)}`} className={buttonCls("ghost", "sm")}>
          <MessagesSquare className="size-3.5" /> Conversations
        </Link>
        <Button size="sm" variant="ghost" className="ml-auto text-danger" icon={<Trash2 className="size-3.5" />} onClick={onRevoke}>
          Revoke
        </Button>
      </div>
    </div>
  );
}

function Info({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted">{label}</dt>
      <dd className="truncate">{children}</dd>
    </div>
  );
}

function ServerUrlBox({ url }: { url?: string }) {
  const [copied, setCopied] = useState(false);
  if (!url) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (http) */
    }
  };
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3 text-sm">
      <span className="text-muted">
        <b className="font-medium text-fg">server_url</b> for the watch setup portal:
      </span>
      <code className="min-w-0 basis-full rounded bg-surface-2 px-2 py-1 font-mono text-xs break-all select-all sm:flex-1 sm:basis-0">{url}</code>
      <Button size="sm" variant="ghost" icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} onClick={copy}>
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function AddWatchDialog({
  open,
  pending,
  onClose,
  onPaired,
  onRefresh,
}: {
  open: boolean;
  pending: PendingPairing[];
  onClose: () => void;
  onPaired: () => void;
  onRefresh: () => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("BuddyAI Watch");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCode("");
    setError(null);
    setDone(false);
    onRefresh();
    const t = window.setInterval(onRefresh, 5000); // expiry is not pushed live
    return () => window.clearInterval(t);
  }, [open, onRefresh]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) return setError(new Error("The code has exactly 6 digits."));
    setBusy(true);
    setError(null);
    try {
      await api.devices.pair(code, name.trim() || "BuddyAI Watch");
      setDone(true);
      onPaired();
      window.setTimeout(onClose, 900);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setError(new Error("Invalid or expired code. Check the watch screen."));
      else setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add watch"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="pair-form" loading={busy} disabled={done}>
            {done ? "Paired" : "Pair"}
          </Button>
        </>
      }
    >
      <form id="pair-form" onSubmit={submit} className="space-y-4">
        <p className="text-sm text-muted">Enter the 6-digit code shown on the watch screen (valid for 5 minutes).</p>
        <Field label="Pairing code" htmlFor="code">
          <Input
            id="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="000000"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            className="h-14 text-center font-mono text-2xl tracking-[0.5em]"
          />
        </Field>
        <Field label="Name" htmlFor="name">
          <Input id="name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
        {done && <p className="text-sm text-ok">Watch paired.</p>}
        <div>
          <p className="mb-2 text-xs font-medium text-muted">Watches waiting ({pending.length})</p>
          {pending.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted">
              No watch in pairing mode. This list updates automatically.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {pending.map((p) => (
                <li key={p.device_id} className="flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs">
                  <OnlineDot online />
                  <span className="min-w-0 flex-1 truncate font-mono">{p.device_id}</span>
                  <span className="text-muted">{p.hw_model}</span>
                  <Badge tone="warn">{Math.max(0, Math.ceil(p.expires_in_s / 60))} min</Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      </form>
    </Dialog>
  );
}

function RenameDialog({ device, onClose, onSaved }: { device: Device | null; onClose: () => void; onSaved: (d: Device) => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (device) {
      setName(device.name);
      setError(null);
    }
  }, [device]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!device || !name.trim()) return;
    setBusy(true);
    try {
      onSaved(await api.devices.rename(device.id, name.trim()));
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={!!device}
      onClose={onClose}
      title="Rename watch"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="rename-form" loading={busy} disabled={!name.trim()}>
            Save
          </Button>
        </>
      }
    >
      <form id="rename-form" onSubmit={submit} className="space-y-3">
        <Field label="Name" htmlFor="rn">
          <Input id="rn" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
