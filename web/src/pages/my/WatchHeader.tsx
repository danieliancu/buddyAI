import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { api, type Device } from "../../api";
import { fmtAgo } from "../../format";
import { BatteryInfo, OnlineDot, StateBadge } from "../../components/DeviceBits";
import { Button, ConfirmDialog, Dialog, ErrorBox, Field, Input, cx } from "../../components/ui";

/** Customer watch header: back link, name (rename), live status, Settings/History tabs, remove. */
export default function WatchHeader({
  id,
  device,
  active,
  note,
  onRenamed,
}: {
  id: string;
  device: Device | null;
  active: "settings" | "history";
  note?: ReactNode;
  onRenamed: (name: string) => void;
}) {
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const base = `/my/watch/${encodeURIComponent(id)}`;

  return (
    <div className="mb-5">
      <Link to="/my" className="mb-3 inline-flex items-center gap-1.5 text-sm font-medium text-muted hover:text-accent">
        <ArrowLeft className="size-4" /> My watches
      </Link>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-[2rem] leading-tight font-bold tracking-tight sm:text-[2.4rem]">{device?.name ?? "Watch"}</h1>
            <button
              onClick={() => setRenaming(true)}
              className="grid size-9 shrink-0 cursor-pointer place-items-center rounded-full bg-surface-2 text-muted hover:text-fg"
              aria-label="Rename watch"
              title="Rename"
            >
              <Pencil className="size-4" />
            </button>
          </div>
          {device && (
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[15px] text-muted">
              <span className="inline-flex items-center gap-2">
                <OnlineDot online={device.online} />
                <StateBadge online={device.online} state={device.state} />
              </span>
              {device.battery_pct != null && <BatteryInfo pct={device.battery_pct} charging={device.charging} />}
              {!device.online && <span>{device.last_seen_at ? `Last seen ${fmtAgo(device.last_seen_at)}` : "Not connected yet"}</span>}
              {note}
            </div>
          )}
        </div>
        <Button
          size="sm"
          variant="danger"
          className="shrink-0"
          icon={<Trash2 className="size-3.5" />}
          aria-label="Remove watch"
          onClick={() => setRemoving(true)}
        >
          <span className="hidden sm:inline">Remove</span>
        </Button>
      </div>

      <div className="mt-5 inline-flex gap-1 rounded-full bg-surface p-1 shadow-(--card-shadow)" role="tablist">
        <Tab to={base} active={active === "settings"}>
          Settings
        </Tab>
        <Tab to={`${base}/history`} active={active === "history"}>
          History
        </Tab>
      </div>

      <RenameDialog
        open={renaming}
        id={id}
        current={device?.name ?? ""}
        onClose={() => setRenaming(false)}
        onSaved={onRenamed}
      />
      <ConfirmDialog
        open={removing}
        danger
        title="Remove watch"
        confirmLabel="Remove watch"
        message={
          <div className="space-y-2">
            <p>
              <b>{device?.name ?? "This watch"}</b> will be removed from your account and its conversation history will be erased.
            </p>
            <p className="text-muted">The watch goes back to the pairing screen. You can add it again later with a new code.</p>
          </div>
        }
        onConfirm={async () => {
          await api.me.devices.remove(id);
          navigate("/my", { replace: true });
        }}
        onClose={() => setRemoving(false)}
      />
    </div>
  );
}

function Tab({ to, active, children }: { to: string; active: boolean; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      role="tab"
      aria-selected={active}
      className={cx(
        "rounded-full px-5 py-2 text-sm transition",
        active ? "bg-accent-bg font-semibold text-accent" : "text-muted hover:text-fg",
      )}
    >
      {children}
    </NavLink>
  );
}

function RenameDialog({
  open,
  id,
  current,
  onClose,
  onSaved,
}: {
  open: boolean;
  id: string;
  current: string;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState(current);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (open) {
      setName(current);
      setError(null);
    }
  }, [open, current]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const d = await api.me.devices.rename(id, name.trim());
      onSaved(d.name);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Rename watch"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="my-rename" loading={busy} disabled={!name.trim()}>
            Save
          </Button>
        </>
      }
    >
      <form id="my-rename" onSubmit={submit} className="space-y-3">
        <Field label="Name" htmlFor="my-rn">
          <Input id="my-rn" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
