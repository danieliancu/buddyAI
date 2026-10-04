import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Bell, CalendarClock, Check, ChevronDown, ChevronRight, MapPin, Pin, Users, CircleCheck, NotebookPen, Pencil, Plus, Trash2, TriangleAlert } from "lucide-react";
import { api, type Item, type ItemKind } from "../../api";
import { Badge, Button, Card, ConfirmDialog, Dialog, Empty, ErrorBox, Field, Input, Select, Spinner, Textarea, useAsync } from "../../components/ui";
import { fmtDateTime, parseDate } from "../../format";
import { useLive } from "../../live";

const NOTE_MAX = 10000;
const REMINDER_MAX = 80;

type Editing = Item | "new" | null;

/** Items of one kind, reloaded whenever the account's notes/reminders change (voice, a watch, another tab). */
function useItems(kind: ItemKind) {
  const all = useAsync(api.me.items.list, []);
  useLive((e) => {
    if (e.type === "items_changed") all.reload();
  });
  return { ...all, items: (all.data ?? []).filter((i) => i.kind === kind) };
}

// ---------- notes ----------

export function MyNotesPage() {
  const q = useItems("note");
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<Item | null>(null);
  // Pinned first (like the watch), then by number.
  const notes = [...q.items].sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned) || a.number - b.number);
  const [pinning, setPinning] = useState<number | null>(null);
  const [pinError, setPinError] = useState<unknown>(null);

  const togglePin = async (n: Item) => {
    setPinning(n.number);
    setPinError(null);
    try {
      await api.me.items.setPinned(n.number, !n.pinned);
      q.reload();
    } catch (err) {
      setPinError(err);
    } finally {
      setPinning(null);
    }
  };

  return (
    <ItemsLayout
      title="Notes"
      intro="Text only, as long as you like. Ask your watch: “note that…”, “show my notes”, “edit note 2”, “delete note 1”."
      onNew={() => setEditing("new")}
      error={q.error}
      onRetry={q.reload}
      loading={q.loading && !q.data}
    >
      <ErrorBox error={pinError} />
      {notes.length === 0 ? (
        <Card>
          <Empty icon={<NotebookPen className="size-7" />} title="No notes yet" />
        </Card>
      ) : (
        <ul className="space-y-3">
          {notes.map((n) => (
            <ItemCard
              key={n.number}
              item={n}
              onEdit={() => setEditing(n)}
              onDelete={() => setDeleting(n)}
              leading={
                <button
                  type="button"
                  onClick={() => togglePin(n)}
                  disabled={pinning === n.number}
                  aria-pressed={!!n.pinned}
                  aria-label={n.pinned ? `Unpin note #${n.number}` : `Pin note #${n.number}`}
                  title={n.pinned ? "Unpin" : "Pin to the top"}
                  className={
                    n.pinned
                      ? "grid size-6 shrink-0 place-items-center rounded-full bg-accent text-white transition disabled:opacity-60"
                      : "grid size-6 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-2 hover:text-fg disabled:opacity-60"
                  }
                >
                  <Pin className="size-3.5" />
                </button>
              }
            >
              <p className="line-clamp-4 text-sm whitespace-pre-line">{n.text}</p>
            </ItemCard>
          ))}
        </ul>
      )}
      <NoteDialog editing={editing} onClose={() => setEditing(null)} onSaved={q.reload} />
      <DeleteDialog item={deleting} onClose={() => setDeleting(null)} onDeleted={q.reload} />
    </ItemsLayout>
  );
}

function NoteDialog({ editing, onClose, onSaved }: { editing: Editing; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState("");
  const form = useSave(editing, onClose, onSaved);

  useEffect(() => {
    if (editing) setText(editing === "new" ? "" : editing.text);
  }, [editing]);

  return (
    <Dialog
      open={!!editing}
      onClose={onClose}
      wide
      title={editing === "new" ? "New note" : `Note #${editing ? editing.number : ""}`}
      footer={<DialogButtons onClose={onClose} busy={form.busy} formId="note-form" />}
    >
      <form
        id="note-form"
        className="space-y-4"
        onSubmit={(e) => form.submit(e, text.trim() ? { kind: "note", text: text.trim(), due_at: null } : "Write something first.")}
      >
        <Field label="Text" htmlFor="ntext" hint={`${text.length.toLocaleString("en-GB")}/${NOTE_MAX.toLocaleString("en-GB")}`}>
          <Textarea id="ntext" rows={12} maxLength={NOTE_MAX} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <ErrorBox error={form.error} />
      </form>
    </Dialog>
  );
}

// ---------- reminders ----------

const dayFmt = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" });
const timeFmt = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

/** "YYYY-MM-DD" of a moment in the browser's time zone (empty without a date). */
function dayKey(d: Date | null): string {
  if (!d) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "Tomorrow · Sun 5 Oct", "Mon 6 Oct". */
function dayLabel(key: string, today: string, tomorrow: string): string {
  const d = new Date(`${key}T12:00:00`);
  if (key === today) return `Today · ${dayFmt.format(d)}`;
  if (key === tomorrow) return `Tomorrow · ${dayFmt.format(d)}`;
  return dayFmt.format(d);
}

/** "in 25 min", "in 2 h 10 min", "tomorrow at 09:00", "Mon 6 Oct at 09:00". */
function untilText(due: Date, now: number, today: string, tomorrow: string): string {
  const min = Math.round((due.getTime() - now) / 60000);
  const key = dayKey(due);
  if (key === today) {
    if (min <= 0) return "now";
    if (min < 60) return `in ${min} min`;
    return `in ${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ""}`;
  }
  return `${key === tomorrow ? "tomorrow" : dayFmt.format(due)} at ${timeFmt.format(due)}`;
}

export function MyRemindersPage() {
  const q = useItems("reminder");
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<Item | null>(null);
  const [toggling, setToggling] = useState<number | null>(null);
  const [toggleError, setToggleError] = useState<unknown>(null);
  const [showPast, setShowPast] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(t);
  }, []);

  const toggleDone = async (r: Item) => {
    setToggling(r.number);
    setToggleError(null);
    try {
      await api.me.items.setDone(r.number, !r.done);
      q.reload();
    } catch (err) {
      setToggleError(err);
    } finally {
      setToggling(null);
    }
  };

  // By time, completed ones last in their day (like the watch).
  const sorted = [...q.items].sort(
    (a, b) =>
      dayKey(parseDate(a.due_at)).localeCompare(dayKey(parseDate(b.due_at))) ||
      Number(a.done) - Number(b.done) ||
      (a.due_at ?? "").localeCompare(b.due_at ?? "") ||
      a.number - b.number,
  );
  const today = dayKey(new Date(now));
  const tomorrow = dayKey(new Date(now + 86400000));
  const keyOf = (r: Item) => dayKey(parseDate(r.due_at)) || today;
  // 1. what is next: the first open reminder from now on (today, or the next day with one)
  const upNext = sorted.find((r) => !r.done && keyOf(r) >= today && (parseDate(r.due_at)?.getTime() ?? now) >= now);
  const todays = sorted.filter((r) => keyOf(r) === today && r !== upNext);
  // 2. the next days, grouped by day
  const coming = new Map<string, Item[]>();
  for (const r of sorted) {
    if (keyOf(r) > today && r !== upNext) coming.set(keyOf(r), [...(coming.get(keyOf(r)) ?? []), r]);
  }
  // 3. the past (newest first; not shown on the watch)
  const past = sorted.filter((r) => keyOf(r) < today).reverse();

  const row = (r: Item, muted = false) => (
    <ReminderRow
      key={r.number}
      r={r}
      muted={muted}
      busy={toggling === r.number}
      onToggle={() => toggleDone(r)}
      onEdit={() => setEditing(r)}
      onDelete={() => setDeleting(r)}
    />
  );

  return (
    <ItemsLayout
      title="Reminders"
      intro="A time and a short text. Ask your watch: “remind me tomorrow at 9 to…”, “show my reminders”, “move reminder 2 to 5 pm”."
      onNew={() => setEditing("new")}
      error={q.error}
      onRetry={q.reload}
      loading={q.loading && !q.data}
    >
      <ErrorBox error={toggleError} />
      {sorted.length === 0 ? (
        <Card>
          <Empty icon={<CalendarClock className="size-7" />} title="No reminders yet" />
        </Card>
      ) : (
        <div className="space-y-8">
          <section aria-labelledby="rem-today">
            <h2 id="rem-today" className="mb-3 text-sm font-semibold text-fg">
              {dayLabel(today, today, tomorrow)}
            </h2>
            {upNext && (
              <UpNextCard
                r={upNext}
                until={untilText(parseDate(upNext.due_at) ?? new Date(now), now, today, tomorrow)}
                busy={toggling === upNext.number}
                onToggle={() => toggleDone(upNext)}
                onEdit={() => setEditing(upNext)}
                onDelete={() => setDeleting(upNext)}
              />
            )}
            {todays.length > 0 ? (
              <ul className={upNext ? "mt-3 space-y-3" : "space-y-3"}>{todays.map((r) => row(r))}</ul>
            ) : (
              !upNext && <p className="text-sm text-muted">Nothing planned for today.</p>
            )}
            {upNext && todays.length === 0 && keyOf(upNext) !== today && (
              <p className="mt-3 text-sm text-muted">Nothing else today.</p>
            )}
          </section>

          {coming.size > 0 && (
            <section aria-labelledby="rem-coming">
              <h2 id="rem-coming" className="mb-3 text-sm font-semibold text-fg">
                Coming up
              </h2>
              <div className="space-y-5">
                {[...coming.entries()].map(([key, items]) => (
                  <div key={key}>
                    <h3 className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">{dayLabel(key, today, tomorrow)}</h3>
                    <ul className="space-y-3">{items.map((r) => row(r))}</ul>
                  </div>
                ))}
              </div>
            </section>
          )}

          {past.length > 0 && (
            <section aria-labelledby="rem-past" className="border-t border-border pt-5">
              <button
                id="rem-past"
                type="button"
                onClick={() => setShowPast((v) => !v)}
                aria-expanded={showPast}
                className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg"
              >
                {showPast ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                Past reminders ({past.length})
                <span className="text-xs">· not shown on the watch</span>
              </button>
              {showPast && <ul className="mt-3 space-y-3">{past.map((r) => row(r, true))}</ul>}
            </section>
          )}
        </div>
      )}
      <ReminderDialog editing={editing} onClose={() => setEditing(null)} onSaved={q.reload} />
      <DeleteDialog item={deleting} onClose={() => setDeleting(null)} onDeleted={q.reload} />
    </ItemsLayout>
  );
}

type RowProps = { r: Item; busy: boolean; onToggle: () => void; onEdit: () => void; onDelete: () => void };

function DoneButton({ r, busy, onToggle }: Pick<RowProps, "r" | "busy" | "onToggle">) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={busy}
      aria-pressed={r.done}
      aria-label={r.done ? `Reopen reminder #${r.number}` : `Complete reminder #${r.number}`}
      title={r.done ? "Reopen" : "Complete"}
      className={
        r.done
          ? "grid size-6 shrink-0 place-items-center rounded-full border border-ok bg-ok text-white transition disabled:opacity-60"
          : "grid size-6 shrink-0 place-items-center rounded-full border-2 border-border text-transparent transition hover:border-ok hover:text-ok disabled:opacity-60"
      }
    >
      <Check className="size-3.5" strokeWidth={3} />
    </button>
  );
}

function ReminderMeta({ r }: { r: Item }) {
  return (
    <>
      {r.notify_before_min ? (
        <span className="inline-flex items-center gap-1 text-muted" title="Advance notice">
          <Bell className="size-3.5" /> {fmtNotice(r.notify_before_min)} before
        </span>
      ) : null}
      {r.done ? (
        <Badge tone="ok">
          <CircleCheck className="size-3" /> Completed
        </Badge>
      ) : (
        r.overdue && (
          <Badge tone="danger">
            <TriangleAlert className="size-3" /> Overdue
          </Badge>
        )
      )}
    </>
  );
}

function ReminderPlace({ r }: { r: Item }) {
  if (!r.location && !r.participants) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {r.location && (
        <span className="inline-flex items-center gap-1">
          <MapPin className="size-3.5" /> {r.location}
        </span>
      )}
      {r.participants && (
        <span className="inline-flex items-center gap-1">
          <Users className="size-3.5" /> {r.participants}
        </span>
      )}
    </div>
  );
}

function timeRange(r: Item): string {
  const start = parseDate(r.due_at);
  return `${start ? timeFmt.format(start) : "—"}${r.end_at ? ` – ${toLocalInput(r.end_at).slice(11)}` : ""}`;
}

/** One reminder in a day's list (the day is in the heading, so only the time is shown). */
function ReminderRow({ r, muted, busy, onToggle, onEdit, onDelete }: RowProps & { muted: boolean }) {
  return (
    <div className={muted ? "opacity-70" : undefined}>
      <ItemCard item={r} onEdit={onEdit} onDelete={onDelete} leading={<DoneButton r={r} busy={busy} onToggle={onToggle} />}>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <CalendarClock className="size-4 text-muted" />
          <span className={r.done ? "font-medium text-muted" : "font-medium"}>{muted ? fmtDateTime(r.due_at) : timeRange(r)}</span>
          <ReminderMeta r={r} />
        </div>
        <p className={r.done ? "mt-1 text-sm text-muted line-through" : "mt-1 text-sm"}>{r.text}</p>
        <ReminderPlace r={r} />
      </ItemCard>
    </div>
  );
}

/** The next thing to do: large, in the accent colour, with the time left. */
function UpNextCard({ r, until, busy, onToggle, onEdit, onDelete }: RowProps & { until: string }) {
  return (
    <div className="rounded-2xl border-2 border-accent bg-accent-bg p-5">
      <div className="flex items-start gap-3">
        <DoneButton r={r} busy={busy} onToggle={onToggle} />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold tracking-wide text-accent uppercase">Up next · {until}</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{timeRange(r)}</p>
          <p className="mt-1 text-base">{r.text}</p>
          <ReminderPlace r={r} />
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono text-xs font-semibold text-accent">#{r.number}</span>
            <ReminderMeta r={r} />
          </div>
        </div>
        <button className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-fg" onClick={onEdit} aria-label={`Edit #${r.number}`}>
          <Pencil className="size-4" />
        </button>
        <button className="rounded p-1.5 text-muted hover:bg-danger-bg hover:text-danger" onClick={onDelete} aria-label={`Delete #${r.number}`}>
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  );
}

/** Advance notice choices (minutes); the assistant can set other values. */
const NOTICE_CHOICES = [5, 10, 15, 30, 60, 120, 1440];

function fmtNotice(min: number): string {
  if (min % 1440 === 0) return `${min / 1440} day${min === 1440 ? "" : "s"}`;
  if (min % 60 === 0) return `${min / 60} h`;
  return min > 60 ? `${Math.floor(min / 60)} h ${min % 60} min` : `${min} min`;
}

/** <input type="datetime-local"> value for a UTC ISO string, in the browser's time zone. */
function toLocalInput(iso: string | null): string {
  const d = parseDate(iso);
  if (!d) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ReminderDialog({ editing, onClose, onSaved }: { editing: Editing; onClose: () => void; onSaved: () => void }) {
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [end, setEnd] = useState(""); // "HH:MM" on the same day, optional
  const [notice, setNotice] = useState(0); // minutes before; 0 = none
  const [location, setLocation] = useState("");
  const [participants, setParticipants] = useState("");
  const form = useSave(editing, onClose, onSaved);

  useEffect(() => {
    if (!editing) return;
    setText(editing === "new" ? "" : editing.text);
    setDue(editing === "new" ? "" : toLocalInput(editing.due_at));
    setEnd(editing === "new" ? "" : toLocalInput(editing.end_at).slice(11));
    setNotice(editing === "new" ? 0 : editing.notify_before_min ?? 0);
    setLocation(editing === "new" ? "" : editing.location ?? "");
    setParticipants(editing === "new" ? "" : editing.participants ?? "");
  }, [editing]);

  const body = () => {
    if (!due) return "Choose when to be reminded.";
    if (!text.trim()) return "Write what to be reminded of.";
    // datetime-local is the browser's local time; new Date() reads it as local, toISOString gives UTC.
    const endAt = end ? `${due.slice(0, 10)}T${end}` : null;
    if (endAt && endAt <= due) return "The end time must be after the start time.";
    return {
      kind: "reminder" as const,
      text: text.trim(),
      due_at: new Date(due).toISOString(),
      end_at: endAt ? new Date(endAt).toISOString() : null,
      notify_before_min: notice || null,
      location: location.trim() || null,
      participants: participants.trim() || null,
    };
  };

  return (
    <Dialog
      open={!!editing}
      onClose={onClose}
      title={editing === "new" ? "New reminder" : `Reminder #${editing ? editing.number : ""}`}
      footer={<DialogButtons onClose={onClose} busy={form.busy} formId="reminder-form" />}
    >
      <form id="reminder-form" className="space-y-4" onSubmit={(e) => form.submit(e, body())}>
        <Field label="When" htmlFor="rdue">
          <Input id="rdue" type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
        </Field>
        <Field label="Until (optional)" htmlFor="rend" hint="For a time range, e.g. 09:30 – 10:00">
          <Input id="rend" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label="Location (optional)" htmlFor="rloc">
          <Input id="rloc" maxLength={120} value={location} onChange={(e) => setLocation(e.target.value)} />
        </Field>
        <Field label="Participants (optional)" htmlFor="rpeople" hint="Names, separated by commas">
          <Input id="rpeople" maxLength={200} value={participants} onChange={(e) => setParticipants(e.target.value)} />
        </Field>
        <Field label="Notify in advance" htmlFor="rnotice" hint="An extra alert before the start; the watch also alerts at the start.">
          <Select id="rnotice" value={notice} onChange={(e) => setNotice(Number(e.target.value))}>
            <option value={0}>No</option>
            {[...new Set([...NOTICE_CHOICES, notice].filter(Boolean))]
              .sort((a, b) => a - b)
              .map((m) => (
                <option key={m} value={m}>
                  {fmtNotice(m)} before
                </option>
              ))}
          </Select>
        </Field>
        <Field label="Text" htmlFor="rtext" hint={`${text.length}/${REMINDER_MAX}`}>
          <Textarea id="rtext" rows={3} className="min-h-0!" maxLength={REMINDER_MAX} value={text} onChange={(e) => setText(e.target.value.replace(/\s*\n\s*/g, " "))} />
        </Field>
        <ErrorBox error={form.error} />
      </form>
    </Dialog>
  );
}

// ---------- shared ----------

function ItemsLayout({
  title,
  intro,
  onNew,
  error,
  onRetry,
  loading,
  children,
}: {
  title: string;
  intro: string;
  onNew: () => void;
  error: unknown;
  onRetry: () => void;
  loading: boolean;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-5 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
          <p className="mt-1 text-sm text-muted">{intro}</p>
        </div>
        <Button variant="primary" icon={<Plus className="size-4" />} onClick={onNew}>
          New
        </Button>
      </div>
      <ErrorBox error={error} onRetry={onRetry} />
      {loading ? <Spinner /> : children}
    </div>
  );
}

function ItemCard({
  item,
  onEdit,
  onDelete,
  leading,
  children,
}: {
  item: Item;
  onEdit: () => void;
  onDelete: () => void;
  /** Before the number, e.g. the reminder's complete button. */
  leading?: ReactNode;
  children: ReactNode;
}) {
  return (
    <li className="rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start gap-3">
        {leading}
        <span className="mt-0.5 font-mono text-sm font-semibold text-accent">#{item.number}</span>
        <div className="min-w-0 flex-1">{children}</div>
        <button
          className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
          onClick={onEdit}
          aria-label={`Edit #${item.number}`}
        >
          <Pencil className="size-4" />
        </button>
        <button
          className="rounded p-1.5 text-muted hover:bg-danger-bg hover:text-danger"
          onClick={onDelete}
          aria-label={`Delete #${item.number}`}
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </li>
  );
}

function DialogButtons({ onClose, busy, formId }: { onClose: () => void; busy: boolean; formId: string }) {
  return (
    <>
      <Button variant="ghost" onClick={onClose}>
        Cancel
      </Button>
      <Button variant="primary" type="submit" form={formId} loading={busy}>
        Save
      </Button>
    </>
  );
}

type ItemBody = {
  kind: ItemKind;
  text: string;
  due_at: string | null;
  end_at?: string | null;
  notify_before_min?: number | null;
  location?: string | null;
  participants?: string | null;
};

/** Create or update; `body` is a string when the form is invalid (shown as the error). */
function useSave(editing: Editing, onClose: () => void, onSaved: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (editing) setError(null);
  }, [editing]);

  const submit = async (e: FormEvent, body: ItemBody | string) => {
    e.preventDefault();
    if (!editing) return;
    if (typeof body === "string") return setError(new Error(body));
    setBusy(true);
    setError(null);
    try {
      if (editing === "new") await api.me.items.create(body);
      else await api.me.items.update(editing.kind, editing.number, body);
      onSaved();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, submit };
}

function DeleteDialog({ item, onClose, onDeleted }: { item: Item | null; onClose: () => void; onDeleted: () => void }) {
  const noun = item?.kind === "reminder" ? "reminder" : "note";
  return (
    <ConfirmDialog
      open={!!item}
      danger
      title={`Delete ${noun}`}
      confirmLabel="Delete"
      message={
        <>
          Delete {noun} <b>#{item?.number}</b>? Its number will be reused by the next new {noun}.
        </>
      }
      onConfirm={async () => {
        if (!item) return;
        await api.me.items.remove(item.kind, item.number);
        onDeleted();
      }}
      onClose={onClose}
    />
  );
}
