import { useState, type FormEvent } from "react";
import { Brain, Check, History, Pencil, Plus, ShieldAlert, Trash2, X } from "lucide-react";
import { api, ApiError, type MemoryItem, type MemoryKind, type MemorySettings } from "../../api";
import { fmtDate } from "../../format";
import { Badge, Button, Card, ConfirmDialog, Dialog, Empty, ErrorBox, Field, Input, Select, Spinner, Toggle, useAsync, PasswordInput } from "../../components/ui";

const KIND_LABEL: Record<MemoryKind, string> = {
  profile: "About you",
  person: "People",
  preference: "Likes",
  routine: "Routines",
  goal: "Goals",
  project: "Projects",
  other: "Other",
};

/** /my/memory: what ola remembers, with correct / confirm / forget, and the two switches. */
export default function MemoryPage() {
  const data = useAsync(api.me.memories.list, []);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [forgetting, setForgetting] = useState<MemoryItem | null>(null);
  const [clearing, setClearing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const d = data.data;
  if (data.loading && !d) return <Spinner />;
  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;
  if (!d || !d.available) {
    return (
      <Card>
        <Empty icon={<Brain className="size-7" />} title="Memory is not available yet" />
      </Card>
    );
  }

  const pending = d.memories.filter((m) => m.status === "pending");
  const active = d.memories.filter((m) => m.status === "active");
  const history = d.memories.filter((m) => m.status === "superseded");
  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      data.reload();
    } catch (e) {
      setError(e);
    }
  };
  const setPref = (body: MemorySettings) =>
    act(async () => {
      const r = await api.me.memories.settings(body);
      data.setData({ ...d, ...r });
    });

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Memory</h1>
          <p className="mt-1 text-sm text-muted">
            Say “remember that…” to your watch and olá keeps it for later conversations. You can correct or forget anything here.
          </p>
        </div>
        <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>
          Add
        </Button>
      </div>

      <ErrorBox error={error} />

      <Card title="Settings">
        <div className="space-y-4">
          <div>
            <Toggle
              checked={d.remember_requests}
              onChange={(v) => void setPref({ remember_requests: v })}
              label={<span className="font-medium">Remember what I ask</span>}
            />
            <p className="mt-1 pl-14 text-xs text-muted">
              When you say “remember that…”, olá keeps it. Off: nothing new is saved (what is already saved stays until you forget it).
            </p>
          </div>
          <div>
            <Toggle
              checked={d.use_memories}
              onChange={(v) => void setPref({ use_memories: v })}
              label={<span className="font-medium">Use my memories in conversations</span>}
            />
            <p className="mt-1 pl-14 text-xs text-muted">
              olá uses what it remembers to give you more personal answers. Off: your memories stay here but are not used.
            </p>
          </div>
          {d.learning_available && (
            <div>
              <Toggle
                checked={d.learn}
                onChange={(v) => void setPref({ learn: v })}
                label={<span className="font-medium">Learn from conversations</span>}
              />
              <p className="mt-1 pl-14 text-xs text-muted">
                After a conversation, olá may note a few lasting facts you mentioned (names, likes, routines). Never health, money, passwords or
                similar. Facts it is unsure about wait here for your confirmation.
              </p>
            </div>
          )}
        </div>
      </Card>

      {pending.length > 0 && (
        <section>
          <h2 className="mb-2 text-sm font-semibold">To confirm</h2>
          <ul className="space-y-2">
            {pending.map((m) => (
              <li key={m.id} className="flex items-start gap-2 rounded-xl border border-warn/40 bg-surface p-3">
                <p className="min-w-0 flex-1 text-sm">{m.fact}</p>
                <Button size="sm" variant="primary" icon={<Check className="size-4" />} onClick={() => void act(() => api.me.memories.confirm(m.id))}>
                  Keep
                </Button>
                <Button size="sm" variant="ghost" icon={<X className="size-4" />} onClick={() => void act(() => api.me.memories.forget(m.id))}>
                  Discard
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-sm font-semibold">
          Remembered <span className="font-normal text-muted">({active.length} of {d.max})</span>
        </h2>
        {active.length === 0 ? (
          <Card>
            <Empty icon={<Brain className="size-7" />} title="Nothing remembered yet">
              Try saying to your watch: “Remember that my granddaughter is called Maria.”
            </Empty>
          </Card>
        ) : (
          <ul className="space-y-2">
            {active.map((m) => (
              <li key={m.id} className="rounded-xl border border-border bg-surface p-3">
                <div className="flex items-start gap-2">
                  <p className="min-w-0 flex-1 text-sm">{m.fact}</p>
                  <button
                    className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
                    onClick={() => setEditing(m)}
                    aria-label="Correct"
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button
                    className="rounded p-1.5 text-muted hover:bg-danger-bg hover:text-danger"
                    onClick={() => setForgetting(m)}
                    aria-label="Forget"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                  <Badge>{KIND_LABEL[m.kind]}</Badge>
                  {m.origin === "inferred" && <Badge tone="accent">Learned{m.confirmed ? ", confirmed" : ""}</Badge>}
                  {m.sensitive && (
                    <Badge tone="warn">
                      <ShieldAlert className="size-3" /> Sensitive
                    </Badge>
                  )}
                  {m.watch !== null && <Badge>Only on {m.watch || "one watch"}</Badge>}
                  {m.valid_until && <span>until {fmtDate(m.valid_until)}</span>}
                  <span>· {fmtDate(m.updated_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {history.length > 0 && (
        <section>
          <button className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg" onClick={() => setShowHistory((v) => !v)}>
            <History className="size-4" /> {showHistory ? "Hide" : "Show"} earlier versions ({history.length})
          </button>
          {showHistory && (
            <ul className="mt-2 space-y-2">
              {history.map((m) => (
                <li key={m.id} className="flex items-start gap-2 rounded-xl border border-border bg-surface/60 p-3 text-sm text-muted">
                  <p className="min-w-0 flex-1 line-through decoration-muted/50">{m.fact}</p>
                  <button
                    className="rounded p-1.5 hover:bg-danger-bg hover:text-danger"
                    onClick={() => setForgetting(m)}
                    aria-label="Forget"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-xs text-muted">Earlier versions are deleted automatically after 90 days.</p>
        </section>
      )}

      {d.memories.length > 0 && (
        <Card className="border-danger/40" title="Forget everything">
          <p className="mb-3 text-sm text-muted">Delete everything olá remembers about you. Your conversation history is not affected.</p>
          <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={() => setClearing(true)}>
            Forget everything…
          </Button>
        </Card>
      )}

      <FactDialog
        open={adding || editing !== null}
        editing={editing}
        kinds={d.kinds}
        onClose={() => {
          setAdding(false);
          setEditing(null);
        }}
        onSaved={data.reload}
      />
      <ConfirmDialog
        open={forgetting !== null}
        title="Forget this?"
        message={<p>“{forgetting?.fact}” will be deleted, with its earlier versions.</p>}
        confirmLabel="Forget"
        danger
        onConfirm={async () => {
          if (forgetting) await api.me.memories.forget(forgetting.id);
          data.reload();
        }}
        onClose={() => setForgetting(null)}
      />
      <ClearDialog open={clearing} onClose={() => setClearing(false)} onDone={data.reload} />
    </div>
  );
}

function FactDialog({
  open,
  editing,
  kinds,
  onClose,
  onSaved,
}: {
  open: boolean;
  editing: MemoryItem | null;
  kinds: MemoryKind[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [fact, setFact] = useState("");
  const [kind, setKind] = useState<MemoryKind>("other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [seen, setSeen] = useState<string | null>(null);
  const key = open ? (editing?.id ?? "new") : null;
  if (key !== seen) {
    setSeen(key);
    setFact(editing?.fact ?? "");
    setKind(editing?.kind ?? "other");
    setError(null);
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (editing) await api.me.memories.update(editing.id, fact.trim(), editing.version);
      else await api.me.memories.add(fact.trim(), kind);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 422 ? new Error(String(err.detail)) : err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? "Correct memory" : "Add a memory"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form="memory-form" loading={busy} disabled={fact.trim().length < 3}>
            Save
          </Button>
        </>
      }
    >
      <form id="memory-form" onSubmit={submit} className="space-y-4">
        <Field label="What should olá remember?" htmlFor="mem-fact" hint="One short fact, e.g. “My granddaughter is called Maria.”">
          <Input id="mem-fact" maxLength={300} value={fact} onChange={(e) => setFact(e.target.value)} />
        </Field>
        {!editing && (
          <Field label="Type" htmlFor="mem-kind">
            <Select id="mem-kind" value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)}>
              {kinds.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <p className="text-xs text-muted">Passwords, PINs, codes, card and bank numbers are never stored.</p>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}

function ClearDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const close = () => {
    setPassword("");
    setError(null);
    onClose();
  };
  const run = async (e?: FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.me.memories.clear(password);
      onDone();
      close();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 403 ? new Error("Wrong password.") : err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Forget everything?"
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button variant="danger" type="submit" form="mem-clear" loading={busy} disabled={!password}>
            Forget everything
          </Button>
        </>
      }
    >
      <form id="mem-clear" onSubmit={run} className="space-y-4 text-sm">
        <p>Everything olá remembers about you is deleted. This cannot be undone.</p>
        <input type="text" autoComplete="username" hidden readOnly />
        <Field label="Enter your password to confirm" htmlFor="mem-pw">
          <PasswordInput id="mem-pw" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorBox error={error} />
      </form>
    </Dialog>
  );
}
