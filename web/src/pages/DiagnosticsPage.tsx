import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Activity, ChevronRight, Cpu, HelpCircle, Server, Stethoscope, Trash2, Wifi } from "lucide-react";
import { api, type Incident, type IncidentCategory, type IncidentDetail } from "../api";
import { useLive } from "../live";
import { fmtAgo, fmtDateTime } from "../format";
import { DevicePicker } from "../components/DeviceBits";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Input, PageHeader, Select, Spinner, cx, useAsync } from "../components/ui";
import {
  CATEGORIES,
  CATEGORY,
  CONFIDENCE,
  SEVERITY,
  apiFilters,
  filtersFromParams,
  flatten,
  hasFilters,
  mergeLive,
  paramsFromFilters,
  techValue,
  type PageFilters,
} from "./diagnostics";

const ICON: Record<IncidentCategory, typeof Cpu> = { watch: Cpu, connection: Wifi, server: Server, undetermined: HelpCircle };
const PAGE = 50;

export default function DiagnosticsPage() {
  const [params, setParams] = useSearchParams();
  const f = filtersFromParams(params);
  const set = (patch: Partial<PageFilters>) => setParams(paramsFromFilters({ ...f, ...patch }), { replace: true });
  const key = paramsFromFilters(f).toString();

  const devices = useAsync(api.devices.list, []);
  const page = useAsync(() => api.incidents.list(apiFilters(f)), [key]);
  const [items, setItems] = useState<Incident[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const keyRef = useRef(key);
  keyRef.current = key;

  useEffect(() => {
    if (page.data) {
      setItems(page.data.items);
      setCursor(page.data.next_cursor);
      setMoreError(null);
    }
  }, [page.data]);

  const { connected } = useLive((e) => {
    if (e.type !== "incident") return;
    const name = devices.data?.find((d) => d.id === e.incident.device_id)?.name ?? e.incident.device_name;
    setItems((l) => mergeLive(l, { ...e.incident, device_name: name }, f, cursor === null));
  });

  const loadMore = async () => {
    if (!cursor) return;
    const asked = key;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const next = await api.incidents.list(apiFilters(f), cursor);
      if (keyRef.current !== asked) return; // the filters changed meanwhile: this page belongs to the old list
      setItems((l) => [...l, ...next.items.filter((n) => !l.some((x) => x.id === n.id))]);
      setCursor(next.next_cursor);
    } catch (e) {
      if (keyRef.current === asked) setMoreError(e);
    } finally {
      setLoadingMore(false);
    }
  };

  const counts = page.data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
  const filtered = hasFilters(f);
  const selected = devices.data?.find((d) => d.id === f.device);

  return (
    <>
      <PageHeader
        title="ola Diagnostics"
        count={page.data ? total : undefined}
        subtitle="Problems on the watches, their connections and the server. Related events are grouped into one incident, and the cause is shown only as far as the evidence goes."
        actions={
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-xs text-muted" title={connected ? "Live updates on" : "Reconnecting…"}>
              <span className={cx("size-2 rounded-full", connected ? "bg-ok" : "bg-border")} />
              {connected ? "Live" : "Offline"}
            </span>
            <Button variant="ghost" icon={<Trash2 className="size-4" />} disabled={items.length === 0} onClick={() => setClearing(true)}>
              Clear
            </Button>
          </div>
        }
      />

      <div className="mb-4 flex overflow-x-auto rounded-lg border border-border bg-surface p-0.5" role="tablist" aria-label="Category">
        <CategoryTab active={!f.category} onClick={() => set({ category: "" })} label="All" count={page.data ? total : undefined} />
        {CATEGORIES.map((c) => (
          <CategoryTab
            key={c.id}
            active={f.category === c.id}
            onClick={() => set({ category: c.id })}
            label={c.label}
            title={c.hint}
            count={page.data ? counts[c.id] ?? 0 : undefined}
          />
        ))}
      </div>

      <Card bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <DevicePicker devices={devices.data ?? []} value={f.device} onChange={(device) => set({ device })} allowAll />
          <Select className="w-auto" value={f.severity} onChange={(e) => set({ severity: e.target.value as PageFilters["severity"] })} aria-label="Severity">
            <option value="">Any severity</option>
            <option value="error">Problems</option>
            <option value="warn">Warnings</option>
            <option value="info">Info</option>
          </Select>
          <Select className="w-auto" value={f.confidence} onChange={(e) => set({ confidence: e.target.value as PageFilters["confidence"] })} aria-label="Confidence">
            <option value="">Any confidence</option>
            <option value="confirmed">Confirmed cause</option>
            <option value="probable">Probable cause</option>
            <option value="unknown">Unknown cause</option>
          </Select>
          <Select className="w-auto" value={f.recovered} onChange={(e) => set({ recovered: e.target.value as PageFilters["recovered"] })} aria-label="Recovery">
            <option value="">Recovered or not</option>
            <option value="no">Not recovered</option>
            <option value="yes">Recovered</option>
          </Select>
          <label className="flex items-center gap-1.5 text-sm text-muted">
            From
            <Input type="date" className="w-auto" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} aria-label="From date" />
          </label>
          <label className="flex items-center gap-1.5 text-sm text-muted">
            To
            <Input type="date" className="w-auto" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} aria-label="To date" />
          </label>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => setParams(paramsFromFilters({ ...filtersFromParams(new URLSearchParams()), category: f.category }), { replace: true })}>
              Reset filters
            </Button>
          )}
        </div>

        <ErrorBox error={page.error} onRetry={page.reload} />
        {page.loading && !page.data ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty icon={<Stethoscope className="size-8" />} title={filtered || f.category ? "No matching incidents" : "No incidents - all quiet"}>
            {filtered || f.category
              ? "Nothing matches these filters. Try a wider date range or another category."
              : "Watches report restarts and lost connections when they reconnect; server problems appear as they happen."}
          </Empty>
        ) : (
          <ul className="divide-y divide-border">
            {items.map((i) => (
              <IncidentRow key={i.id} incident={i} open={open === i.id} onToggle={() => setOpen(open === i.id ? null : i.id)} />
            ))}
          </ul>
        )}
        {(cursor || moreError != null) && (
          <div className="border-t border-border p-3 text-center">
            <ErrorBox error={moreError} onRetry={loadMore} />
            {cursor && (
              <Button variant="secondary" size="sm" loading={loadingMore} onClick={loadMore}>
                Load {PAGE} more
              </Button>
            )}
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={clearing}
        title="Clear incidents"
        message={selected ? `Delete all incidents of ${selected.name}?` : "Delete the incidents of all watches?"}
        confirmLabel="Clear"
        danger
        onConfirm={async () => {
          await api.incidents.clear(f.device || undefined);
          page.reload();
        }}
        onClose={() => setClearing(false)}
      />
    </>
  );
}

function CategoryTab({ active, onClick, label, count, title }: { active: boolean; onClick: () => void; label: string; count?: number; title?: string }) {
  return (
    <button
      role="tab"
      aria-selected={active}
      title={title}
      onClick={onClick}
      className={cx(
        "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition",
        active ? "bg-accent-bg font-medium text-accent" : "text-muted hover:text-fg",
      )}
    >
      {label}
      {count !== undefined && <span className={cx("tabular text-xs", active ? "text-accent" : "text-muted")}>{count}</span>}
    </button>
  );
}

function IncidentRow({ incident: i, open, onToggle }: { incident: Incident; open: boolean; onToggle: () => void }) {
  const cat = CATEGORY[i.category] ?? CATEGORY.undetermined;
  const sev = SEVERITY[i.severity] ?? SEVERITY.warn;
  const Icon = ICON[i.category] ?? HelpCircle;
  return (
    <li>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface-2">
        <ChevronRight className={cx("mt-1 size-4 shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <Icon className="mt-0.5 size-5 shrink-0 text-muted" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium">{i.title}</span>
            {i.related_count > 0 && <span className="text-xs text-muted">+{i.related_count} related</span>}
          </div>
          <p className="mt-0.5 line-clamp-2 text-sm text-muted">{i.cause}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span title={fmtDateTime(i.occurred_at)}>
              {fmtDateTime(i.occurred_at)} · {fmtAgo(i.occurred_at)}
            </span>
            <span className="max-w-48 truncate" title={i.device_id}>
              {i.device_name ?? i.device_id}
            </span>
            {i.fw_version && <span className="font-mono">fw {i.fw_version}</span>}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 sm:flex-row sm:items-center sm:gap-1.5">
          <Badge tone={cat.tone}>{cat.label}</Badge>
          <Badge tone={sev.tone}>{sev.label}</Badge>
          <span className="hidden md:inline">
            <Badge tone="neutral">{CONFIDENCE[i.confidence]?.label ?? i.confidence}</Badge>
          </span>
          <RecoveryBadge incident={i} />
        </div>
      </button>
      {open && <IncidentDetails incident={i} />}
    </li>
  );
}

function RecoveryBadge({ incident: i }: { incident: Incident }) {
  if (i.recovered_at) return <Badge tone="ok">Recovered</Badge>;
  if (i.legacy) return <Badge tone="neutral">Not tracked</Badge>;
  return <Badge tone="warn">Open</Badge>;
}

const QUESTIONS: [keyof IncidentDetail["explanation"], string][] = [
  ["what", "What happened?"],
  ["where", "Where did it happen?"],
  ["cause", "What caused it?"],
  ["affected", "What was affected?"],
  ["recovered", "Has it recovered?"],
  ["next_step", "What should I do next?"],
];

function IncidentDetails({ incident }: { incident: Incident }) {
  const [view, setView] = useState<"simple" | "technical">("simple");
  // Reloads when live evidence regroups the incident (updated_at changes).
  const detail = useAsync(() => api.incidents.get(incident.id), [incident.id, incident.updated_at]);
  const d = detail.data;
  return (
    <div className="border-t border-border bg-surface-2/50 px-4 py-4 sm:pl-16">
      <div className="mb-3 flex items-center gap-3">
        <div className="flex rounded-lg border border-border bg-surface p-0.5" role="tablist" aria-label="View">
          {(["simple", "technical"] as const).map((v) => (
            <button
              key={v}
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={cx("rounded-md px-3 py-1 text-sm transition", view === v ? "bg-accent-bg font-medium text-accent" : "text-muted hover:text-fg")}
            >
              {v === "simple" ? "Simple" : "Technical"}
            </button>
          ))}
        </div>
        <Link to={`/admin/devices/${encodeURIComponent(incident.device_id)}`} className="text-sm text-accent hover:underline">
          Open watch
        </Link>
      </div>
      <ErrorBox error={detail.error} onRetry={detail.reload} />
      {!d ? (
        detail.loading && <Spinner />
      ) : view === "simple" ? (
        <dl className="grid gap-3 sm:grid-cols-2">
          {QUESTIONS.map(([k, q]) => (
            <div key={k}>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted">{q}</dt>
              <dd className="mt-0.5 text-sm">{d.explanation[k]}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <TechnicalView d={d} />
      )}
    </div>
  );
}

function TechnicalView({ d }: { d: IncidentDetail }) {
  const facts: [string, string | number | null | undefined][] = [
    ["Reason code", d.reason_code],
    ["Category / component", `${d.category}${d.suspected_component ? ` / ${d.suspected_component}` : ""}`],
    ["Confidence", d.confidence],
    ["Detected by", d.detected_by],
    ["Session", d.session_id],
    ["Turn", d.turn_id],
    ["Firmware", d.fw_version],
    ["Recovered", d.recovered_at ? fmtDateTime(d.recovered_at) : null],
  ];
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
        {facts
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="w-40 shrink-0 text-muted">{k}</dt>
              <dd className="min-w-0 break-all font-mono text-[12px]">{v}</dd>
            </div>
          ))}
      </dl>
      {d.legacy && <p className="text-sm text-muted">Recorded before ola Diagnostics: only the original issue's fields are available.</p>}
      <div>
        <h4 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
          <Activity className="size-3.5" /> Timeline
        </h4>
        <ol className="space-y-2">
          {d.events.map((e) => (
            <li key={e.id} className={cx("rounded-lg border bg-surface p-3", e.primary ? "border-accent" : "border-border")}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <span className="font-mono text-[12px] text-muted">{fmtDateTime(e.occurred_at)}</span>
                <Badge tone="neutral">{e.detected_by === "watch" ? "Watch report" : e.detected_by === "server" ? "Server" : "Issue"}</Badge>
                <span className="font-medium">{e.title}</span>
                {e.primary && <span className="text-xs text-accent">cause</span>}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Chip k="kind" v={e.reason ? `${e.kind} / ${e.reason}` : e.kind} />
                {e.reason_code && <Chip k="code" v={e.reason_code} />}
                {e.turn_id != null && <Chip k="turn" v={String(e.turn_id)} />}
                {flatten(e.detail).map(([k, v]) => (
                  <Chip key={k} k={k} v={techValue(k, v)} />
                ))}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function Chip({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex max-w-full items-baseline gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[11px]">
      <span className="text-muted">{k}</span>
      <span className="break-all">{v}</span>
    </span>
  );
}
