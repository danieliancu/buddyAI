import { useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";
import { api, type Currency, type Device, type Diagnostics, type PricingRule, type Stats, type Usage } from "../api";
import { useLive } from "../live";
import { useLanguages } from "../languages";
import { KIND_LABEL, STATUS_LABEL, langLabel, langName, langNative, UNIT_LABEL, fmtDateTime, fmtMoney, fmtMs, fmtQty, fmtUsd } from "../format";
import { DevicePicker, TurnStatusBadge } from "../components/DeviceBits";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, PageHeader, Spinner, Table, cx, useAsync } from "../components/ui";
import { FinanceTab, PlanSettingsTab } from "./FinanceTabs";

const PERIODS = [
  { days: 1, label: "24 h" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const TABS = [
  { id: "cost", label: "Cost" },
  { id: "finance", label: "Finance" },
  { id: "diag", label: "Performance" },
  { id: "pricing", label: "Pricing" },
  { id: "plan", label: "Plan settings" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export default function UsagePage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get("tab"))?.id ?? "cost") as Tab;
  const days = Number(params.get("days")) || 7;
  const deviceId = params.get("device") ?? "";
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };

  const devices = useAsync(api.devices.list, []);

  return (
    <>
      <PageHeader title="Usage & performance" subtitle="Estimated provider usage and cost, and response latency (TTFA)." />
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-border bg-surface p-0.5" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setParam("tab", t.id === "cost" ? "" : t.id)}
              className={cx("rounded-md px-3 py-1.5 text-sm transition", tab === t.id ? "bg-accent-bg font-medium text-accent" : "text-muted hover:text-fg")}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab !== "pricing" && tab !== "plan" && (
          <>
            <div className="flex rounded-lg border border-border bg-surface p-0.5">
              {PERIODS.map((p) => (
                <button
                  key={p.days}
                  onClick={() => setParam("days", String(p.days))}
                  className={cx("rounded-md px-2.5 py-1.5 text-sm transition", days === p.days ? "bg-surface-2 font-medium text-fg" : "text-muted hover:text-fg")}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {tab !== "finance" && (
              <DevicePicker devices={devices.data ?? []} value={deviceId} onChange={(id) => setParam("device", id)} allowAll />
            )}
          </>
        )}
      </div>
      {tab === "cost" && <CostTab days={days} deviceId={deviceId} />}
      {tab === "diag" && <DiagTab days={days} deviceId={deviceId} devices={devices.data ?? []} />}
      {tab === "pricing" && <PricingTab />}
      {tab === "finance" && <FinanceTab days={days} />}
      {tab === "plan" && <PlanSettingsTab />}
    </>
  );
}

// ---------------------------------------------------------------- cost

function CostTab({ days, deviceId }: { days: number; deviceId: string }) {
  const usage = useAsync(() => api.usage(days, deviceId || undefined), [days, deviceId]);
  useLive((e) => e.type === "turn_end" && usage.reload());
  if (usage.error) return <ErrorBox error={usage.error} onRetry={usage.reload} />;
  if (!usage.data) return <Spinner />;
  const u = usage.data;
  const money = (v: number | null | undefined) => fmtMoney(v, u.currency);
  const unpriced = u.items.filter((i) => i.unpriced > 0).length;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Estimated total cost"
          value={money(u.total_cost)}
          sub={`last ${days === 1 ? "24 h" : `${days} days`} · ${fmtUsd(u.total_cost_usd)} billed in USD`}
        />
        <StatTile label="Average cost / day" value={money(u.total_cost / days)} />
        <StatTile
          label="Unpriced"
          value={String(unpriced)}
          sub={unpriced ? "rows without a price – cost is underestimated" : "all rows are priced"}
          tone={unpriced ? "warn" : undefined}
        />
      </div>
      <Card title={`Cost per day (${u.currency})`}>
        <CostChart usage={u} days={days} />
      </Card>
      <Card title="Usage by provider" bodyClassName="p-0 px-4">
        {u.items.length === 0 ? (
          <Empty title="No usage in the selected period" />
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Unit</th>
                <th className="text-right">Quantity</th>
                <th className="text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {u.items.map((i) => (
                <tr key={`${i.kind}|${i.provider}|${i.model}|${i.unit}`}>
                  <td>
                    <Badge>{KIND_LABEL[i.kind] ?? i.kind}</Badge>
                  </td>
                  <td>{i.provider}</td>
                  <td className="font-mono text-xs">{i.model}</td>
                  <td className="text-muted">{UNIT_LABEL[i.unit] ?? i.unit}</td>
                  <td className="tabular text-right">{fmtQty(i.quantity)}</td>
                  <td className="tabular text-right">
                    <span title={`${fmtUsd(i.cost_usd)} (USD)`}>{money(i.cost)}</span>
                    {i.unpriced > 0 && (
                      <Badge tone="warn" className="ml-2">
                        <AlertTriangle className="size-3" /> unpriced ({i.unpriced})
                      </Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <EstimateNote rate={u} />
    </div>
  );
}

function CostChart({ usage, days }: { usage: Usage; days: number }) {
  // Fill every day of the period so gaps read as zero, not as missing bars.
  const data = useMemo(() => {
    const map = new Map(usage.by_day.map((d) => [d.day, d.cost]));
    const out: { day: string; label: string; cost: number }[] = [];
    const n = Math.max(days, 1);
    const today = new Date();
    for (let i = n - 1; i >= 0; i--) {
      const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
      const key = d.toISOString().slice(0, 10);
      out.push({ day: key, label: `${d.getUTCDate()}/${d.getUTCMonth() + 1}`, cost: map.get(key) ?? 0 });
    }
    return out;
  }, [usage, days]);
  if (!usage.by_day.length) return <Empty title="No cost recorded in the selected period" />;
  const money = (v: number) => fmtMoney(v, usage.currency);
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeOpacity={0.6} />
          <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={{ stroke: "var(--border)" }} minTickGap={16} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={64} tickFormatter={(v: number) => money(v)} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--fg)" }}
            labelStyle={{ color: "var(--muted)" }}
            labelFormatter={(_, p) => (p?.[0]?.payload as { day?: string } | undefined)?.day ?? ""}
            formatter={(v) => [money(Number(v)), "Cost"]}
          />
          <Bar dataKey="cost" fill="var(--accent)" radius={[4, 4, 0, 0]} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------- diagnostics

const STAGES: { key: keyof Diagnostics["stages"]; label: string; hint: string }[] = [
  { key: "stt_ms", label: "STT", hint: "end of speech → final transcript" },
  { key: "llm_first_token_ms", label: "LLM first token", hint: "request → first token" },
  { key: "tts_first_audio_ms", label: "TTS first audio", hint: "first text → first audio" },
  { key: "ttfa_server_ms", label: "TTFA server", hint: "end of speech → first frame sent" },
  { key: "ttfa_device_ms", label: "TTFA device", hint: "end of speech → first frame on the watch" },
];

function DiagTab({ days, deviceId, devices }: { days: number; deviceId: string; devices: Device[] }) {
  const diag = useAsync(() => api.diagnostics(Math.min(days, 90), deviceId || undefined), [days, deviceId]);
  const langs = useLanguages();
  useLive((e) => e.type === "turn_end" && diag.reload());
  if (diag.error) return <ErrorBox error={diag.error} onRetry={diag.reload} />;
  if (!diag.data) return <Spinner />;
  const d = diag.data;
  const names = new Map(devices.map((x) => [x.id, x.name]));
  const totalTurns = Object.values(d.status_counts).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <TtfaTile label="TTFA p50" value={d.ttfa.p50} target={d.targets.ttfa_p50_ms} />
        <TtfaTile label="TTFA p95" value={d.ttfa.p95} target={d.targets.ttfa_p95_ms} />
        <StatTile
          label="Turns"
          value={String(totalTurns)}
          sub={
            <span className="flex flex-wrap gap-1.5">
              {Object.entries(d.status_counts).map(([s, n]) => (
                <Badge key={s} tone={s === "completed" ? "ok" : s === "error" ? "danger" : s === "aborted" ? "warn" : "neutral"}>
                  {STATUS_LABEL[s] ?? s}: {n}
                </Badge>
              ))}
            </span>
          }
        />
      </div>
      <p className="flex items-start gap-2 text-xs text-muted">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        TTFA = time from end of speech to the first audio on the watch (or sent by the server if the watch did not report). Completed turns only.
      </p>

      <Card title="Latency by stage" bodyClassName="p-0 px-4">
        <Table>
          <thead>
            <tr>
              <th>Stage</th>
              <th className="text-right">Turns</th>
              <th className="text-right">p50</th>
              <th className="text-right">p95</th>
            </tr>
          </thead>
          <tbody>
            {STAGES.map((s) => (
              <StatsRow key={s.key} label={<>{s.label}<span className="block text-[11px] text-muted">{s.hint}</span></>} stats={d.stages[s.key]} />
            ))}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="TTFA by language" bodyClassName="p-0 px-4">
          <StatsTable rows={Object.entries(d.by_language).map(([k, v]) => [langLabel(k, langs), v])} target={d.targets} />
        </Card>
        <Card title="TTFA by provider (STT / LLM / TTS)" bodyClassName="p-0 px-4">
          <StatsTable rows={Object.entries(d.by_provider).map(([k, v]) => [<span className="font-mono text-xs">{k}</span>, v])} target={d.targets} />
        </Card>
      </div>

      <Card title="Recent turns" bodyClassName="p-0 px-4">
        {d.recent.length === 0 ? (
          <Empty title="No turns in the selected period" />
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Watch</th>
                <th>Lang</th>
                <th>Status</th>
                <th className="text-right">STT</th>
                <th className="text-right">LLM</th>
                <th className="text-right">TTS</th>
                <th className="text-right">TTFA</th>
              </tr>
            </thead>
            <tbody>
              {d.recent.map((t) => (
                <tr key={t.id}>
                  <td className="whitespace-nowrap">{fmtDateTime(t.created_at)}</td>
                  <td className="max-w-40 truncate">{names.get(t.device_id) ?? t.device_id}</td>
                  <td className="whitespace-nowrap" title={t.language === "auto" ? "No speech detected" : langName(t.language, langs)}>
                    {t.language === "auto" ? "—" : langNative(t.language, langs)}
                  </td>
                  <td>
                    {t.status === "completed" ? <Badge tone="ok">completed</Badge> : <TurnStatusBadge status={t.status} />}
                    {t.error && <span className="block max-w-60 truncate text-[11px] text-danger" title={t.error}>{t.error}</span>}
                  </td>
                  <td className="tabular text-right">{fmtMs(t.stt_ms)}</td>
                  <td className="tabular text-right">{fmtMs(t.llm_first_token_ms)}</td>
                  <td className="tabular text-right">{fmtMs(t.tts_first_audio_ms)}</td>
                  <td className={cx("tabular text-right font-medium", t.ttfa_ms != null && t.ttfa_ms > d.targets.ttfa_p95_ms && "text-danger")}>
                    {fmtMs(t.ttfa_ms)}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function StatsRow({ label, stats, target }: { label: ReactNode; stats: Stats; target?: Diagnostics["targets"] }) {
  const bad50 = target && stats.p50 != null && stats.p50 > target.ttfa_p50_ms;
  const bad95 = target && stats.p95 != null && stats.p95 > target.ttfa_p95_ms;
  return (
    <tr>
      <td>{label}</td>
      <td className="tabular text-right text-muted">{stats.count}</td>
      <td className={cx("tabular text-right", bad50 && "text-danger")}>{fmtMs(stats.p50)}</td>
      <td className={cx("tabular text-right", bad95 && "text-danger")}>{fmtMs(stats.p95)}</td>
    </tr>
  );
}

function StatsTable({ rows, target }: { rows: [ReactNode, Stats][]; target: Diagnostics["targets"] }) {
  if (!rows.length) return <Empty title="No data" />;
  return (
    <Table>
      <thead>
        <tr>
          <th></th>
          <th className="text-right">Turns</th>
          <th className="text-right">p50</th>
          <th className="text-right">p95</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, s], i) => (
          <StatsRow key={i} label={label} stats={s} target={target} />
        ))}
      </tbody>
    </Table>
  );
}

function TtfaTile({ label, value, target }: { label: string; value: number | null; target: number }) {
  const ok = value != null && value <= target;
  return (
    <div className={cx("rounded-xl border bg-surface p-4", value == null ? "border-border" : ok ? "border-ok/40" : "border-danger/40")}>
      <div className="flex items-center justify-between text-xs text-muted">
        {label}
        {value != null &&
          (ok ? (
            <Badge tone="ok">
              <CheckCircle2 className="size-3" /> on target
            </Badge>
          ) : (
            <Badge tone="danger">
              <XCircle className="size-3" /> over target
            </Badge>
          ))}
      </div>
      <div className="tabular mt-2 text-3xl font-semibold tracking-tight">{value == null ? "—" : `${(value / 1000).toFixed(2)} s`}</div>
      <div className="mt-1 text-xs text-muted">target &lt; {(target / 1000).toFixed(1)} s</div>
    </div>
  );
}

function StatTile({ label, value, sub, tone }: { label: string; value: string; sub?: ReactNode; tone?: "warn" }) {
  return (
    <div className={cx("rounded-xl border bg-surface p-4", tone === "warn" ? "border-warn/40" : "border-border")}>
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("tabular mt-2 text-3xl font-semibold tracking-tight", tone === "warn" && "text-warn")}>{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- pricing

// Prices are stored per unit (USD); shown per 1M tokens/characters or per minute of audio so they are readable.
function unitScale(unit: string): { factor: number; label: string } {
  if (unit === "audio_second") return { factor: 60, label: "per minute" };
  if (unit === "input_token") return { factor: 1_000_000, label: "per 1M input tokens" };
  if (unit === "output_token") return { factor: 1_000_000, label: "per 1M output tokens" };
  if (unit === "character") return { factor: 1_000_000, label: "per 1M characters" };
  return { factor: 1, label: `per ${unit}` };
}

function PricingTab() {
  const rules = useAsync(api.pricing.list, []);
  const cur = useAsync(api.pricing.currency, []);
  if (rules.error || cur.error) return <ErrorBox error={rules.error ?? cur.error} onRetry={() => { rules.reload(); cur.reload(); }} />;
  if (!rules.data || !cur.data) return <Spinner />;
  const currency = cur.data;
  return (
    <div className="space-y-4">
      <CurrencyCard
        value={currency}
        onSaved={(c) => {
          cur.setData(c);
          rules.reload();
        }}
      />
      <EstimateNote rate={currency} />
      <Card title="Pricing rules" bodyClassName="p-0 px-4">
        <Table>
          <thead>
            <tr>
              <th>Provider / model</th>
              <th>Price (USD, as billed)</th>
              <th>In {currency.currency}</th>
              <th>Note</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rules.data.map((r) => (
              <PriceRow
                key={r.id}
                rule={r}
                currency={currency}
                onSaved={(nr) => rules.setData((l) => l?.map((x) => (x.id === nr.id ? { ...nr, price_display: nr.price_usd * currency.usd_rate } : x)) ?? l)}
              />
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}

function PriceRow({ rule, currency, onSaved }: { rule: PricingRule; currency: Currency; onSaved: (r: PricingRule) => void }) {
  const { factor, label } = unitScale(rule.unit);
  const toText = (perUnit: number) => String(Number((perUnit * factor).toPrecision(6)));
  const initial = toText(rule.price_usd);
  const [price, setPrice] = useState(initial);
  const [note, setNote] = useState(rule.note);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const num = Number(price.replace(",", "."));
  const valid = price.trim() !== "" && Number.isFinite(num) && num >= 0;
  const perUnit = valid ? num / factor : NaN;
  const dirty = price.trim() !== initial || note !== rule.note;

  const save = async () => {
    if (!valid) return setErr("Invalid price");
    setBusy(true);
    setErr(null);
    try {
      const r = await api.pricing.update(rule.id, perUnit, note);
      onSaved(r);
      setPrice(toText(r.price_usd));
      setNote(r.note);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr className="align-top">
      <td>
        <div>{rule.provider}</div>
        <div className="font-mono text-[11px] text-muted">{rule.model}</div>
      </td>
      <td>
        <div className="flex items-center gap-1.5">
          <span className="text-sm text-muted">$</span>
          <Input
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            inputMode="decimal"
            aria-label={`Price ${label}`}
            className={cx("h-9 w-28 font-mono text-xs", !valid && "border-danger")}
          />
        </div>
        <div className="mt-1 text-[11px] text-muted">{label}</div>
        {err && <div className="text-[11px] text-danger">{err}</div>}
      </td>
      <td className="tabular pt-3.5 whitespace-nowrap">
        {valid ? fmtMoney(num * currency.usd_rate, currency.currency) : "—"}
        <div className="text-[11px] text-muted">{label}</div>
      </td>
      <td>
        <Input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" className="h-9 min-w-64 text-xs" />
      </td>
      <td>
        <Button size="sm" variant={dirty ? "primary" : "ghost"} disabled={!dirty} loading={busy} onClick={save}>
          {saved ? "Saved" : "Save"}
        </Button>
      </td>
    </tr>
  );
}

function CurrencyCard({ value, onSaved }: { value: Currency; onSaved: (c: Currency) => void }) {
  const [rate, setRate] = useState(String(value.usd_rate));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const num = Number(rate.replace(",", "."));
  const valid = Number.isFinite(num) && num > 0;
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const c = await api.pricing.setCurrency(value.currency, num);
      setRate(String(c.usd_rate));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1500);
      onSaved(c);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Currency">
      <div className="flex flex-wrap items-end gap-3">
        <Field label={`1 USD = … ${value.currency}`} htmlFor="usd-rate" error={valid ? undefined : "Must be greater than 0"}>
          <div className="flex items-center gap-2">
            <Input id="usd-rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} className="w-32 font-mono" />
            <span className="text-sm text-muted">{value.symbol}</span>
          </div>
        </Field>
        <Button variant="primary" loading={busy} disabled={!valid || num === value.usd_rate} onClick={save}>
          {saved ? "Saved" : "Save rate"}
        </Button>
      </div>
      <p className="mt-3 text-xs text-muted">Vendors bill in USD; amounts shown are converted at this rate.</p>
      <div className="mt-2">
        <ErrorBox error={err} />
      </div>
    </Card>
  );
}

function EstimateNote({ rate }: { rate: Currency }) {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted">
      <Info className="mt-0.5 size-3.5 shrink-0" />
      <span>
        Costs are estimates computed from the pricing rules at the time of each turn and converted at 1 USD = {rate.usd_rate} {rate.currency}.
        Verify prices in the provider consoles (OpenAI, Alibaba Model Studio, Azure). Changing a price does not recalculate costs already
        recorded.
      </span>
    </p>
  );
}
