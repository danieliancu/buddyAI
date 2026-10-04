import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Check, Copy, MessagesSquare, Pencil, Plus, SlidersHorizontal, Trash2, UserRound, Watch } from "lucide-react";
import { api, ApiError, type Device, type LiveEvent, type PendingPairing } from "../api";
import { useLive } from "../live";
import { fmtAgo, fmtDateTime } from "../format";
import { BatteryInfo, OnlineDot, RssiInfo, StateBadge } from "../components/DeviceBits";
import { AccountPicker } from "../components/AccountPicker";
import { Badge, Button, Card, ConfirmDialog, Dialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, Table, useAsync } from "../components/ui";

export default function DevicesPage() {
  const devices = useAsync(api.devices.list, []);
  const info = useAsync(api.system.info, []);
  const [pending, setPending] = useState<PendingPairing[]>([]);
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Device | null>(null);
  const [revoking, setRevoking] = useState<Device | null>(null);
  const [assigning, setAssigning] = useState<Device | null>(null);
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
        count={devices.data ? list.length : undefined}
        subtitle={`Paired watches, their owners and live status.${devices.data ? ` ${onlineCount} online now.` : ""}`}
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
        <Card bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>Watch</th>
                <th>Owner</th>
                <th>Status</th>
                <th>Battery</th>
                <th>Signal</th>
                <th>Firmware</th>
                <th>Last seen</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((d) => (
                <DeviceRow
                  key={d.id}
                  device={d}
                  onRename={() => setRenaming(d)}
                  onRevoke={() => setRevoking(d)}
                  onAssign={() => setAssigning(d)}
                />
              ))}
            </tbody>
          </Table>
        </Card>
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
      <AssignDialog
        device={assigning}
        onClose={() => setAssigning(null)}
        onSaved={(d) => update(d.id, { account: d.account ?? null })}
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

/** One watch per row: who owns it, live status and telemetry, actions. */
function DeviceRow({
  device: d,
  onRename,
  onRevoke,
  onAssign,
}: {
  device: Device;
  onRename: () => void;
  onRevoke: () => void;
  onAssign: () => void;
}) {
  const icon = "rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg";
  return (
    <tr>
      <td>
        <div className="flex items-center gap-2.5">
          <OnlineDot online={d.online} />
          <div className="min-w-0">
            <p className="max-w-48 truncate font-medium">{d.name}</p>
            <p className="max-w-48 truncate font-mono text-[11px] text-muted" title={d.id}>
              {d.id}
            </p>
          </div>
        </div>
      </td>
      <td>
        {d.account ? (
          <Link
            to={`/admin/customers/${d.account.id}`}
            className="block max-w-52 truncate text-fg underline decoration-border underline-offset-4 hover:decoration-fg"
            title={d.account.name || d.account.email}
          >
            {d.account.email}
          </Link>
        ) : (
          <span className="text-muted">Stock</span>
        )}
      </td>
      <td>
        <StateBadge online={d.online} state={d.state} />
      </td>
      <td className="whitespace-nowrap">
        <BatteryInfo pct={d.battery_pct} charging={d.charging} />
      </td>
      <td className="whitespace-nowrap">
        <RssiInfo rssi={d.rssi} />
      </td>
      <td className="font-mono text-xs">{d.fw_version || "—"}</td>
      <td className="whitespace-nowrap" title={fmtDateTime(d.last_seen_at)}>
        {d.online ? "now" : fmtAgo(d.last_seen_at)}
      </td>
      <td>
        <div className="flex items-center justify-end gap-0.5">
          <Link to={`/admin/devices/${encodeURIComponent(d.id)}`} className={icon} title="Settings" aria-label="Settings">
            <SlidersHorizontal className="size-4" />
          </Link>
          <Link to={`/admin/conversations?device=${encodeURIComponent(d.id)}`} className={icon} title="Conversations" aria-label="Conversations">
            <MessagesSquare className="size-4" />
          </Link>
          <button onClick={onAssign} className={icon} title="Assign to customer" aria-label="Assign to customer">
            <UserRound className="size-4" />
          </button>
          <button onClick={onRename} className={icon} title="Rename" aria-label="Rename">
            <Pencil className="size-4" />
          </button>
          <button onClick={onRevoke} className="rounded-md p-1.5 text-muted hover:bg-danger-bg hover:text-danger" title="Revoke" aria-label="Revoke">
            <Trash2 className="size-4" />
          </button>
        </div>
      </td>
    </tr>
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
  const [name, setName] = useState("ola Watch");
  const [accountId, setAccountId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCode("");
    setAccountId(null);
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
      await api.devices.pair(code, name.trim() || "ola Watch", accountId);
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
        <Field label="Customer (optional)" htmlFor="pair-acc" hint="Leave as stock to assign the watch later.">
          <AccountPicker id="pair-acc" value={accountId} onChange={setAccountId} />
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

function AssignDialog({ device, onClose, onSaved }: { device: Device | null; onClose: () => void; onSaved: (d: Device) => void }) {
  const [accountId, setAccountId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (device) {
      setAccountId(device.account?.id ?? null);
      setError(null);
    }
  }, [device]);
  const current = device?.account?.id ?? null;
  const changed = accountId !== current;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!device || !changed) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.devices.assign(device.id, accountId));
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
      title="Assign to customer"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={current != null ? "danger" : "primary"} type="submit" form="assign-form" loading={busy} disabled={!changed}>
            Assign
          </Button>
        </>
      }
    >
      <form id="assign-form" onSubmit={submit} className="space-y-4">
        <p className="text-sm">
          <b>{device?.name}</b> is currently {device?.account ? <>owned by <b>{device.account.email}</b></> : "in stock"}.
        </p>
        <Field label="New owner" htmlFor="assign-acc">
          <AccountPicker id="assign-acc" value={accountId} onChange={setAccountId} noneLabel="Stock (no customer)" />
        </Field>
        {current != null && changed && (
          <p className="rounded-lg border border-danger/30 bg-danger-bg px-3 py-2 text-sm text-danger">
            The previous owner's history on this watch will be erased.
          </p>
        )}
        <p className="text-xs text-muted">The watch reconnects under its new owner. Its settings (voice, colours, custom instructions) are kept.</p>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
