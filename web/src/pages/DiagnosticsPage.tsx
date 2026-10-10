import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, Cpu, HelpCircle, Search, Server, Trash2, Wifi } from "lucide-react";
import { api, type Incident, type IncidentCategory, type IncidentDetail, type IncidentStats } from "../api";
import { useLive } from "../live";
import { fmtAgo, fmtDateTime } from "../format";
import { DevicePicker } from "../components/DeviceBits";
import { Badge, Button, ConfirmDialog, Empty, ErrorBox, Input, PageHeader, Select, Spinner, cx, useAsync } from "../components/ui";
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
const CHARTS: IncidentCategory[] = ["watch", "connection", "server"];
/** One toolbar control: half the row on a phone, compact on a wider screen. */
const BOX = "w-[calc(50%-0.25rem)] sm:w-44";

/** A white panel like the rest of the dashboard: title row, optional subtitle, content. */
function Panel({ title, subtitle, icon, aside, children, className }: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-border bg-surface", className)}>
      {title && (
        <header className="flex items-start justify-between gap-3 px-4 pt-4">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              {icon}
              {title}
            </h2>
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

export default function DiagnosticsPage() {
  const [params, setParams] = useSearchParams();
  const f = filtersFromParams(params);
  const set = (patch: Partial<PageFilters>) => setParams(paramsFromFilters({ ...f, ...patch }), { replace: true });
  const key = paramsFromFilters(f).toString();

  const devices = useAsync(api.devices.list, []);
  const page = useAsync(() => api.incidents.list(apiFilters(f)), [key]);
  const stats = useAsync(() => api.incidents.stats(24, f.device || undefined), [f.device]);
  const [items, setItems] = useState<Incident[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [search, setSearch] = useState(f.q);
  const keyRef = useRef(key);
  keyRef.current = key;
  const detailsRef = useRef<HTMLDivElement>(null);
  const select = (id: number) => {
    setSelected(id);
    // One column (phone, tablet): the details are below the list - bring them into view.
    if (window.matchMedia?.("(max-width: 1023px)").matches) {
      requestAnimationFrame(() => detailsRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
    }
  };

  useEffect(() => {
    if (page.data) {
      setItems(page.data.items);
      setCursor(page.data.next_cursor);
      setMoreError(null);
    }
  }, [page.data]);

  // Search: debounced into the URL.
  useEffect(() => {
    if (search === f.q) return;
    const t = setTimeout(() => set({ q: search }), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  // The detail panel shows the selected incident, or the newest one.
  const current = items.find((i) => i.id === selected) ?? items[0] ?? null;

  // Live: regrouped / new incidents update the list at once; the charts follow a moment later.
  const statsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { connected } = useLive((e) => {
    if (e.type !== "incident") return;
    const name = devices.data?.find((d) => d.id === e.incident.device_id)?.name ?? e.incident.device_name;
    setItems((l) => mergeLive(l, { ...e.incident, device_name: name }, f, cursor === null));
    if (statsTimer.current) clearTimeout(statsTimer.current);
    statsTimer.current = setTimeout(stats.reload, 2000);
  });
  useEffect(() => () => {
    if (statsTimer.current) clearTimeout(statsTimer.current);
  }, []);

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
  const shown = f.category ? counts[f.category] ?? 0 : total;
  const filtered = hasFilters(f) || Boolean(f.category);
  const selectedDevice = devices.data?.find((d) => d.id === f.device);

  return (
    <>
      <PageHeader
        title="olá Diagnostics"
        subtitle="Problems on the watches, their connections and the server. Related events are grouped into one incident, and the cause is shown only as far as the evidence goes."
        actions={
          <span className="flex items-center gap-1.5 text-xs text-muted" title={connected ? "Live updates on" : "Reconnecting…"}>
            <span className={cx("size-2 rounded-full", connected ? "bg-ok" : "bg-border")} />
            {connected ? "Live" : "Offline"}
          </span>
        }
      />

      {/* toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-60">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
          <Input
            type="search"
            className="pl-9"
            placeholder="Search watch or cause…"
            value={search}
            maxLength={64}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search"
          />
        </div>
        <div className={BOX}>
        <Select value={f.category} onChange={(e) => set({ category: e.target.value as PageFilters["category"] })} aria-label="Category">
          <option value="">All categories</option>
          {CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </Select>
        </div>
        <div className={cx(BOX, "[&_select]:w-full [&_select]:min-w-0")}>
          <DevicePicker devices={devices.data ?? []} value={f.device} onChange={(device) => set({ device })} allowAll />
        </div>
        <div className={BOX}>
        <Select value={f.severity} onChange={(e) => set({ severity: e.target.value as PageFilters["severity"] })} aria-label="Severity">
          <option value="">All severities</option>
          <option value="error">Problems</option>
          <option value="warn">Warnings</option>
          <option value="info">Info</option>
        </Select>
        </div>
        <div className={BOX}>
        <Select value={f.recovered} onChange={(e) => set({ recovered: e.target.value as PageFilters["recovered"] })} aria-label="Recovery">
          <option value="">All statuses</option>
          <option value="no">Open</option>
          <option value="yes">Recovered</option>
        </Select>
        </div>
        <div className={BOX}>
        <Select value={f.confidence} onChange={(e) => set({ confidence: e.target.value as PageFilters["confidence"] })} aria-label="Confidence">
          <option value="">Any confidence</option>
          <option value="confirmed">Confirmed cause</option>
          <option value="probable">Probable cause</option>
          <option value="unknown">Unknown cause</option>
        </Select>
        </div>
        <div className={BOX} title="From">
          <Input type="date" value={f.from} max={f.to || undefined} onChange={(e) => set({ from: e.target.value })} aria-label="From date" />
        </div>
        <div className={BOX} title="To">
          <Input type="date" value={f.to} min={f.from || undefined} onChange={(e) => set({ to: e.target.value })} aria-label="To date" />
        </div>
        {filtered && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearch("");
              setParams(new URLSearchParams(), { replace: true });
            }}
          >
            Reset
          </Button>
        )}
        <Button className="sm:ml-auto" variant="secondary" size="sm" icon={<Trash2 className="size-4" />} disabled={items.length === 0} onClick={() => setClearing(true)}>
          Clear
        </Button>
      </div>

      <NeedsAttention
        stats={stats.data}
        onPick={(a) => {
          setSearch("");
          setSelected(a.latest_id);
          setParams(paramsFromFilters({ ...filtersFromParams(new URLSearchParams()), category: a.category, device: a.device_id, recovered: "no" }), { replace: true });
        }}
      />

      <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        {CHARTS.map((c) => (
          <TrendCard key={c} category={c} stats={stats.data} active={f.category === c} onClick={() => set({ category: f.category === c ? "" : c })} />
        ))}
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <Panel
          className="min-w-0"
          title="Incidents"
          subtitle="Grouped by where the cause is. Select one to see the details."
          aside={page.data && <span className="shrink-0 text-xs text-muted">{items.length} of {shown} shown</span>}
        >
          <div className="mt-3">
            <ErrorBox error={page.error} onRetry={page.reload} />
            {page.loading && !page.data ? (
              <Spinner />
            ) : items.length === 0 ? (
              <Empty icon={<CheckCircle2 className="size-8" />} title={filtered ? "No matching incidents" : "No incidents - all quiet"}>
                {filtered
                  ? "Nothing matches these filters. Try a wider date range or another category."
                  : "Watches report restarts and lost connections when they reconnect; server problems appear as they happen."}
              </Empty>
            ) : (
              CATEGORIES.filter((c) => items.some((i) => i.category === c.id)).map((c) => (
                <CategorySection
                  key={c.id}
                  category={c.id}
                  total={counts[c.id] ?? 0}
                  items={items.filter((i) => i.category === c.id)}
                  selected={current?.id ?? null}
                  onSelect={select}
                />
              ))
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
          </div>
        </Panel>

        <div ref={detailsRef} className="min-w-0 scroll-mt-4 space-y-4 lg:sticky lg:top-4">{current ? <IncidentPanels incident={current} /> : null}</div>
      </div>

      <ConfirmDialog
        open={clearing}
        title="Clear incidents"
        message={selectedDevice ? `Delete all incidents of ${selectedDevice.name}?` : "Delete the incidents of all watches?"}
        confirmLabel="Clear"
        danger
        onConfirm={async () => {
          await api.incidents.clear(f.device || undefined);
          page.reload();
          stats.reload();
        }}
        onClose={() => setClearing(false)}
      />
    </>
  );
}

// ------------------------------------------------------------------ needs attention

function NeedsAttention({ stats, onPick }: { stats: IncidentStats | null; onPick: (a: IncidentStats["attention"][number]) => void }) {
  if (!stats) return null;
  const items = stats.attention;
  return (
    <Panel
      className="mb-4"
      title="Needs attention"
      icon={<AlertTriangle className={cx("size-4", items.length ? "text-danger" : "text-muted")} />}
      subtitle="Open problems from the last 7 days, grouped by watch"
      aside={
        stats.open_total > 0 && (
          <span className="rounded-md bg-danger px-2 py-0.5 text-xs font-semibold text-white" aria-label={`${stats.open_total} open`}>
            {stats.open_total}
          </span>
        )
      }
    >
      <div className="p-4 pt-3">
        {items.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted">
            <CheckCircle2 className="size-4 text-ok" /> Nothing needs attention: every recent problem has recovered.
          </p>
        ) : (
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {items.map((a) => {
              const err = a.severity === "error";
              return (
                <li key={`${a.category}-${a.device_id}`} className="min-w-0">
                  <button
                    type="button"
                    onClick={() => onPick(a)}
                    className={cx(
                      "w-full rounded-lg border px-3 py-2.5 text-left transition hover:brightness-[0.98]",
                      err ? "border-danger/20 bg-danger-bg" : "border-warn/20 bg-warn-bg",
                    )}
                  >
                    <span className="flex items-center justify-between text-xs font-medium">
                      <span className={err ? "text-danger" : "text-warn"}>{CATEGORY[a.category]?.label ?? a.category}</span>
                      <span className={cx("tabular", err ? "text-danger" : "text-warn")}>{a.count}</span>
                    </span>
                    <span className="mt-1 block truncate text-sm">
                      {a.title} on {a.device_name ?? a.device_id}
                    </span>
                    <span className="mt-0.5 block text-[11px] text-muted">{fmtAgo(a.latest_at)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------ trend cards

function TrendCard({ category, stats, active, onClick }: { category: IncidentCategory; stats: IncidentStats | null; active: boolean; onClick: () => void }) {
  const c = CATEGORY[category];
  const Icon = ICON[category];
  const data = useMemo(() => (stats?.buckets ?? []).map((b) => ({ t: b.t, n: b[category] })), [stats, category]);
  const gid = `diag-grad-${category}`;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "min-w-0 rounded-xl border bg-surface p-4 text-left transition hover:border-fg/20",
        active ? "border-accent ring-1 ring-accent" : "border-border",
      )}
    >
      <span className="flex items-start justify-between gap-2">
        <span>
          <span className="flex items-center gap-2 text-[15px] font-semibold">
            <Icon className="size-4 text-muted" />
            {c.label} incidents
          </span>
          <span className="mt-0.5 block text-xs text-muted">{c.hint}</span>
        </span>
        <span className="tabular text-lg font-semibold">{stats ? stats.totals[category] : "–"}</span>
      </span>
      <span className="mt-3 block h-24">
        {stats && (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={c.color} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={c.color} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <YAxis hide allowDecimals={false} domain={[0, (max: number) => Math.max(2, max)]} />
              <Tooltip
                cursor={{ stroke: "var(--border)" }}
                contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid var(--border)" }}
                labelFormatter={(_, p) => (p?.[0] ? fmtDateTime(String(p[0].payload.t)) : "")}
                formatter={(v) => [String(v), "incidents"]}
              />
              <Area type="basis" dataKey="n" stroke={c.color} strokeWidth={2} fill={`url(#${gid})`} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </span>
      <span className="mt-2 flex justify-between text-[11px] text-muted">
        <span>Last 24 hours</span>
        <span>Now</span>
      </span>
    </button>
  );
}

// ------------------------------------------------------------------ incident list

function CategorySection({ category, total, items, selected, onSelect }: {
  category: IncidentCategory;
  total: number;
  items: Incident[];
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  const c = CATEGORY[category];
  const Icon = ICON[category];
  return (
    <div className="border-t border-border first:border-t-0">
      <div className="flex items-center justify-between px-4 py-2.5">
        <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
          <Icon className="size-3.5 text-muted" />
          {c.label}
        </span>
        <span className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted">
          {total} {total === 1 ? "incident" : "incidents"}
        </span>
      </div>
      <ul>
        {items.map((i) => (
          <IncidentRow key={i.id} incident={i} active={i.id === selected} onSelect={() => onSelect(i.id)} />
        ))}
      </ul>
    </div>
  );
}

function statusOf(i: Incident): { dot: string; label: string } {
  if (i.recovered_at) return { dot: "bg-ok", label: "Recovered" };
  if (i.legacy) return { dot: "bg-border", label: "Not tracked" };
  return { dot: "bg-warn", label: "Open" };
}

function IncidentRow({ incident: i, active, onSelect }: { incident: Incident; active: boolean; onSelect: () => void }) {
  const Icon = ICON[i.category] ?? HelpCircle;
  const sev = SEVERITY[i.severity] ?? SEVERITY.warn;
  const st = statusOf(i);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active || undefined}
        className={cx("flex w-full items-start gap-3 border-t border-border px-4 py-3 text-left transition", active ? "bg-accent-bg" : "hover:bg-surface-2")}
      >
        <span className="relative mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg border border-border bg-surface">
          <Icon className="size-4 text-muted" />
          {i.related_count > 0 && (
            <span
              className="absolute -right-1.5 -top-1.5 grid size-4 place-items-center rounded-full bg-danger text-[10px] font-semibold text-white"
              title={`${i.related_count} related events`}
            >
              {i.related_count > 9 ? "9+" : i.related_count}
            </span>
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium">{i.title}</span>
            <span className={cx("size-2 rounded-full", st.dot)} title={st.label} />
            <span className="rounded-md border border-border px-1.5 py-px text-[11px] text-muted">
              {CONFIDENCE[i.confidence]?.label ?? i.confidence}
            </span>
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted">
            {i.device_name ?? i.device_id} • {fmtAgo(i.occurred_at)}
            {i.fw_version ? ` • fw ${i.fw_version}` : ""}
            {i.related_count > 0 ? ` • +${i.related_count} related` : ""}
          </span>
          <span className="mt-1 line-clamp-1 block text-xs text-muted">{i.cause}</span>
        </span>
        <span className="shrink-0">
          <Badge tone={sev.tone}>{sev.label}</Badge>
        </span>
      </button>
    </li>
  );
}

// ------------------------------------------------------------------ detail panels

const QUESTIONS: [keyof IncidentDetail["explanation"], string][] = [
  ["what", "What happened?"],
  ["where", "Where did it happen?"],
  ["cause", "What caused it?"],
  ["affected", "What was affected?"],
  ["recovered", "Has it recovered?"],
  ["next_step", "What should I do next?"],
];

function IncidentPanels({ incident }: { incident: Incident }) {
  const [view, setView] = useState<"simple" | "technical">("simple");
  // Reloads when live evidence regroups the incident (updated_at changes).
  const detail = useAsync(() => api.incidents.get(incident.id), [incident.id, incident.updated_at]);
  const d = detail.data?.id === incident.id ? detail.data : null;
  const Icon = ICON[incident.category] ?? HelpCircle;
  const st = statusOf(incident);
  const cat = CATEGORY[incident.category];

  return (
    <>
      <Panel title="Incident details" subtitle="Cause, evidence and what to do">
        <div className="p-4 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2.5">
              <Icon className="mt-0.5 size-5 shrink-0 text-muted" />
              <div className="min-w-0">
                <p className="font-medium leading-snug">{incident.title}</p>
                <p className="text-xs text-muted">
                  {incident.device_name ?? incident.device_id} · {fmtDateTime(incident.occurred_at)}
                </p>
              </div>
            </div>
            <span className="flex shrink-0 items-center gap-1.5 text-xs">
              <span className={cx("size-2 rounded-full", st.dot)} />
              {st.label}
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Badge tone={cat.tone}>{cat.label}</Badge>
            <Badge tone={(SEVERITY[incident.severity] ?? SEVERITY.warn).tone}>{(SEVERITY[incident.severity] ?? SEVERITY.warn).label}</Badge>
            <Badge tone="neutral">{CONFIDENCE[incident.confidence]?.label ?? incident.confidence}</Badge>
          </div>

          <div className="mt-4 flex rounded-lg border border-border bg-surface-2 p-0.5" role="tablist" aria-label="View">
            {(["simple", "technical"] as const).map((v) => (
              <button
                key={v}
                role="tab"
                aria-selected={view === v}
                onClick={() => setView(v)}
                className={cx("flex-1 rounded-md px-3 py-1 text-sm transition", view === v ? "bg-surface font-medium shadow-sm" : "text-muted hover:text-fg")}
              >
                {v === "simple" ? "Simple" : "Technical"}
              </button>
            ))}
          </div>

          <ErrorBox error={detail.error} onRetry={detail.reload} />
          {!d ? (
            detail.loading && <Spinner />
          ) : view === "simple" ? (
            <dl className="mt-4 space-y-3">
              {QUESTIONS.map(([k, q]) => (
                <div key={k}>
                  <dt className="text-[11px] font-semibold uppercase tracking-wide text-muted">{q}</dt>
                  <dd className="mt-0.5 text-sm">{d.explanation[k]}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <TechnicalDetails d={d} />
          )}
          <Link to={`/admin/devices/${encodeURIComponent(incident.device_id)}`} className="mt-4 inline-block text-sm text-accent hover:underline">
            Open watch
          </Link>
        </div>
      </Panel>

      <Panel title="Timeline" subtitle="Every event of this incident, oldest first">
        <div className="p-4 pt-3">
          {!d ? (
            detail.loading && <Spinner />
          ) : (
            <ol className="space-y-3">
              {d.events.map((e) => (
                <li key={e.id} className="flex gap-2.5">
                  <span className={cx("mt-1.5 size-2 shrink-0 rounded-full", e.primary ? "bg-accent" : "bg-border")} />
                  <div className="min-w-0 text-sm">
                    <p className="leading-snug">
                      {e.title}
                      {e.primary && <span className="ml-1.5 text-xs text-accent">cause</span>}
                    </p>
                    <p className="text-xs text-muted">
                      {e.detected_by === "watch" ? "Reported by the watch" : e.detected_by === "server" ? "Seen by the server" : "Issue"} ·{" "}
                      <span title={fmtDateTime(e.occurred_at)}>{fmtAgo(e.occurred_at)}</span>
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </Panel>
    </>
  );
}

function TechnicalDetails({ d }: { d: IncidentDetail }) {
  const facts: [string, string | number | null | undefined][] = [
    ["Reason code", d.reason_code],
    ["Category", d.category],
    ["Component", d.suspected_component],
    ["Confidence", d.confidence],
    ["Detected by", d.detected_by],
    ["Session", d.session_id],
    ["Turn", d.turn_id],
    ["Firmware", d.fw_version],
    ["Recovered", d.recovered_at ? fmtDateTime(d.recovered_at) : null],
  ];
  return (
    <div className="mt-4 space-y-4">
      <div>
        <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Details</h3>
        <dl className="divide-y divide-border text-sm">
          {facts
            .filter(([, v]) => v !== null && v !== undefined && v !== "")
            .map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3 py-1.5">
                <dt className="text-muted">{k}</dt>
                <dd className="min-w-0 break-all text-right font-mono text-[12px]">{v}</dd>
              </div>
            ))}
        </dl>
      </div>
      {d.legacy && <p className="text-sm text-muted">Recorded before olá Diagnostics: only the original issue's fields are available.</p>}
      <div>
        <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">Evidence</h3>
        <div className="space-y-2">
          {d.events.map((e) => (
            <div key={e.id} className={cx("rounded-lg border p-2.5", e.primary ? "border-accent" : "border-border")}>
              <p className="text-xs font-medium">
                {e.kind}
                {e.reason ? ` / ${e.reason}` : ""}
                <span className="ml-1.5 font-normal text-muted">{fmtDateTime(e.occurred_at)}</span>
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {e.reason_code && <Chip k="code" v={e.reason_code} />}
                {e.turn_id != null && <Chip k="turn" v={String(e.turn_id)} />}
                {flatten(e.detail).map(([k, v]) => (
                  <Chip key={k} k={k} v={techValue(k, v)} />
                ))}
              </div>
            </div>
          ))}
        </div>
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
