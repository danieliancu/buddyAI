import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, Info } from "lucide-react";
import { api, type BillingSettings, type CostStatus, type TurnCostStats } from "../api";
import { useLive } from "../live";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Select, Spinner, Table, Toggle, cx, useAsync } from "../components/ui";

const gbp = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `£${v.toFixed(digits)}`);
const small = (v: number | null | undefined) => (v == null ? "—" : v < 0.01 ? `${(v * 100).toFixed(2)}p` : `£${v.toFixed(3)}`);

const GROUP_LABEL: Record<string, string> = {
  stt: "Speech-to-text",
  tts: "Text-to-speech",
  search: "Web search",
  llm_input: "LLM input",
  llm_cached_input: "LLM cached input",
  llm_output: "LLM output",
  llm_other: "LLM other",
  llm: "LLM",
  embedding: "Embeddings",
  other: "Other",
};

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: ReactNode; tone?: "warn" | "ok" | "danger" }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className={cx("tabular mt-1 text-2xl font-semibold tracking-tight", tone === "warn" && "text-warn", tone === "ok" && "text-ok", tone === "danger" && "text-danger")}>
        {value}
      </p>
      {sub && <p className="mt-1 text-xs text-muted">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- finance

export function FinanceTab({ days }: { days: number }) {
  const q = useAsync(() => api.finance(days), [days]);
  useLive((e) => e.type === "turn_end" && q.reload());
  if (q.error) return <ErrorBox error={q.error} onRetry={q.reload} />;
  if (!q.data) return <Spinner />;
  const f = q.data;
  const revenueKinds = Object.entries(f.revenue.by_kind);
  const period = days === 1 ? "24 h" : `${days} days`;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Net revenue (ex VAT)" value={gbp(f.revenue.net)} sub={`Stripe · last ${period}`} />
        <Tile label="Estimated provider cost" value={gbp(f.provider_cost, 4)} sub="AI providers only · mock usage excluded" />
        <Tile
          label="Gross contribution"
          value={gbp(f.gross_contribution)}
          sub="net revenue − provider cost, before every other operating expense"
          tone={f.gross_contribution < 0 ? "danger" : "ok"}
        />
        <Tile
          label="Unpriced usage"
          value={String(f.unpriced.reduce((n, u) => n + u.rows, 0))}
          sub={f.unpriced.length ? "rows without a pricing rule — cost is underestimated" : "every row is priced"}
          tone={f.unpriced.length ? "warn" : undefined}
        />
      </div>

      {f.unpriced.length > 0 && (
        <Card title={<span className="inline-flex items-center gap-2 text-warn"><AlertTriangle className="size-4" /> Missing pricing rules</span>} bodyClassName="p-0 px-4">
          <Table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Unit</th>
                <th className="text-right">Rows</th>
                <th className="text-right">Quantity</th>
              </tr>
            </thead>
            <tbody>
              {f.unpriced.map((u) => (
                <tr key={`${u.provider}|${u.model}|${u.unit}`}>
                  <td>{u.provider}</td>
                  <td className="font-mono text-xs">{u.model}</td>
                  <td>{u.unit}</td>
                  <td className="tabular text-right">{u.rows}</td>
                  <td className="tabular text-right">{u.quantity}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="py-3 text-xs text-muted">Add the prices in the Pricing tab; until then these costs are not counted.</p>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Provider cost by component">
          <Breakdown rows={Object.entries(f.by_group).map(([k, v]) => [GROUP_LABEL[k] ?? k, v])} total={f.provider_cost} />
        </Card>
        <Card title="Revenue by source">
          {revenueKinds.length === 0 ? (
            <Empty title="No payments in this period" />
          ) : (
            <Table>
              <thead>
                <tr>
                  <th>Source</th>
                  <th className="text-right">Gross</th>
                  <th className="text-right">VAT</th>
                  <th className="text-right">Net</th>
                </tr>
              </thead>
              <tbody>
                {revenueKinds.map(([k, v]) => (
                  <tr key={k}>
                    <td className="capitalize">{k === "topup" ? "Extra usage" : k === "watch" ? "Watches" : k}</td>
                    <td className="tabular text-right">{gbp(v.gross)}</td>
                    <td className="tabular text-right text-muted">{gbp(v.vat)}</td>
                    <td className="tabular text-right">{gbp(v.net)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          {Object.keys(f.revenue.other_currencies).length > 0 && (
            <p className="mt-2 text-xs text-muted">
              Not in the totals (other currencies):{" "}
              {Object.entries(f.revenue.other_currencies).map(([c, v]) => `${v.toFixed(2)} ${c.toUpperCase()}`).join(", ")}
            </p>
          )}
        </Card>
      </div>

      <Card title="Cost per interaction">
        <Table>
          <thead>
            <tr>
              <th>Outcome</th>
              <th className="text-right">Count</th>
              <th className="text-right">Total</th>
              <th className="text-right">Mean</th>
              <th className="text-right">p50</th>
              <th className="text-right">p90</th>
              <th className="text-right">p95</th>
            </tr>
          </thead>
          <tbody>
            <StatsRow label="Completed" s={f.turns.completed} />
            <StatsRow label="Aborted" s={f.turns.aborted} />
            <StatsRow label="Failed (not charged)" s={f.turns.error} />
            <StatsRow label="No speech" s={f.turns.no_speech} />
          </tbody>
        </Table>
        <p className="mt-2 text-xs text-muted">
          Failed interactions are not counted against customers' allowances ({gbp(f.turns.not_charged_to_customers, 4)} in this
          period); their cost stays in the operator totals.
        </p>
      </Card>

      <div className="grid gap-3 sm:grid-cols-4">
        <Tile label="Web searches" value={String(f.search.searches)} sub={`${gbp(f.search.cost, 4)} in search fees`} />
        <Tile label="Answered from cache" value={String(f.search.cache_hits)} />
        <Tile label="Cache hit rate" value={f.search.hit_rate == null ? "—" : `${Math.round(f.search.hit_rate * 100)}%`} />
        <Tile label="Search fees avoided" value={gbp(f.search.avoided_at_least, 4)} sub="at least (search tokens come on top)" tone="ok" />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Provider cost per day (GBP)">
          <TrendChart data={f.by_day.map((d) => ({ label: d.day.slice(5), cost: d.cost }))} />
        </Card>
        <Card title="Provider cost per month (GBP)">
          <TrendChart data={f.by_month.map((d) => ({ label: d.month, cost: d.cost }))} />
        </Card>
      </div>

      <AccountCostsCard />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Cost per customer" bodyClassName="p-0 px-4">
          <Breakdown rows={f.by_account.map((a) => [a.account, a.cost])} total={f.provider_cost} />
        </Card>
        <Card title="Cost per watch" bodyClassName="p-0 px-4">
          <Breakdown rows={f.by_device.map((d) => [d.name, d.cost])} total={f.provider_cost} />
        </Card>
      </div>

      <p className="flex items-start gap-2 text-xs text-muted">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        {f.note} Revenue is what Stripe reported; VAT is shown apart. Gross contribution is not profit: hardware, hosting, payment
        fees, support and other costs are not included.
      </p>
    </div>
  );
}

const COST_STATUS: Record<CostStatus, { label: string; tone: "ok" | "neutral" | "warn" | "danger" }> = {
  within: { label: "Within target", tone: "ok" },
  approaching: { label: "Approaching target", tone: "warn" },
  over: { label: "Over target", tone: "danger" },
  critical: { label: "Critical cost", tone: "danger" },
};
const COST_SORTS = [
  ["cost", "AI cost"],
  ["projected", "Projected"],
  ["average", "Per interaction"],
  ["interactions", "Interactions"],
] as const;

/** Internal: each customer's AI cost in their current billing period against the monthly target, and the
 * projected cost at their interaction allowance. Monitoring only - it never limits a customer. */
export function AccountCostsCard() {
  const [status, setStatus] = useState<CostStatus | "">("");
  const [sort, setSort] = useState<(typeof COST_SORTS)[number][0]>("cost");
  const q = useAsync(() => api.accountCosts({ status: status || undefined, sort }), [status, sort]);
  const d = q.data;
  return (
    <Card
      title="AI cost per account (current billing period)"
      actions={
        <div className="flex flex-wrap gap-2">
          <Select className="w-auto" value={status} onChange={(e) => setStatus(e.target.value as CostStatus | "")} aria-label="Status">
            <option value="">All statuses</option>
            {(Object.keys(COST_STATUS) as CostStatus[]).map((s) => (
              <option key={s} value={s}>
                {COST_STATUS[s].label} ({d?.status_counts[s] ?? 0})
              </option>
            ))}
          </Select>
          <Select className="w-auto" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort by">
            {COST_SORTS.map(([k, label]) => (
              <option key={k} value={k}>
                Sort: {label}
              </option>
            ))}
          </Select>
        </div>
      }
      bodyClassName="p-0 px-4"
    >
      <ErrorBox error={q.error} onRetry={q.reload} />
      {!d ? (
        q.loading && <Spinner />
      ) : d.accounts.length === 0 ? (
        <Empty title="No accounts match" />
      ) : (
        <Table>
          <thead>
            <tr>
              <th>Account</th>
              <th className="text-right">Interactions</th>
              <th className="text-right">AI cost</th>
              <th className="text-right">Per interaction</th>
              <th className="text-right" title="Average × the account's allowance. An estimate.">
                Projected at limit*
              </th>
              <th>Status</th>
              <th>Breakdown</th>
            </tr>
          </thead>
          <tbody>
            {d.accounts.map((a) => (
              <tr key={a.account_id}>
                <td>
                  <Link to={`/admin/customers/${a.account_id}`} className="text-accent hover:underline">
                    {a.account ?? `#${a.account_id}`}
                  </Link>
                </td>
                <td className="tabular text-right">
                  {a.interactions.toLocaleString("en-GB")} / {a.limit.toLocaleString("en-GB")}{" "}
                  <span className="text-xs text-muted">({a.used_pct}%)</span>
                </td>
                <td className="tabular text-right" title={`Interactive ${gbp(a.interactive_cost, 4)} · background ${gbp(a.background_cost, 4)}`}>
                  {gbp(a.cost, 2)}
                  {a.unpriced_rows > 0 && <span className="ml-1 text-xs text-warn" title={`${a.unpriced_rows} usage rows without a price: the real cost is higher`}>+?</span>}
                </td>
                <td className="tabular text-right">{a.average_cost == null ? "—" : small(a.average_cost)}</td>
                <td className="tabular text-right">
                  {a.projected_cost == null ? (
                    <span className="text-xs text-muted" title={`Fewer than ${d.min_sample} interactions: no reliable estimate yet`}>
                      not enough data
                    </span>
                  ) : (
                    <span className={a.projected_cost >= a.target ? "text-warn" : ""}>
                      ~{gbp(a.projected_cost, 2)}
                      {a.projection === "incomplete" && <span className="ml-1 text-xs text-warn">(incomplete)</span>}
                    </span>
                  )}
                </td>
                <td>
                  <Badge tone={COST_STATUS[a.status].tone}>{COST_STATUS[a.status].label}</Badge>
                </td>
                <td className="text-xs text-muted">
                  {Object.entries(a.by_group)
                    .map(([g, v]) => `${GROUP_LABEL[g] ?? g} ${gbp(v, 2)}`)
                    .join(" · ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {d && (
        <p className="py-3 text-xs text-muted">
          Internal target {gbp(d.thresholds.target, 2)} per account per month (warning from {gbp(d.thresholds.warn, 2)}, critical from{" "}
          {gbp(d.thresholds.critical, 2)}). Monitoring only: customers can always use all their interactions. *Projections are
          estimates (average cost per interaction × the account's allowance), shown from {d.min_sample} interactions on.
        </p>
      )}
    </Card>
  );
}

function StatsRow({ label, s }: { label: string; s: TurnCostStats }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="tabular text-right">{s.count}</td>
      <td className="tabular text-right">{s.count ? gbp(s.total, 4) : "—"}</td>
      <td className="tabular text-right">{small(s.mean)}</td>
      <td className="tabular text-right">{small(s.p50)}</td>
      <td className="tabular text-right">{small(s.p90)}</td>
      <td className="tabular text-right">{small(s.p95)}</td>
    </tr>
  );
}

function Breakdown({ rows, total }: { rows: [string, number][]; total: number }) {
  if (!rows.length) return <Empty title="No usage in this period" />;
  return (
    <ul className="divide-y divide-border">
      {rows.map(([label, v]) => {
        const pct = total > 0 ? (v / total) * 100 : 0;
        return (
          <li key={label} className="py-2.5">
            <div className="mb-1 flex items-center justify-between gap-2 text-sm">
              <span className="truncate">{label}</span>
              <span className="tabular shrink-0">
                {gbp(v, 4)} <span className="text-xs text-muted">({pct.toFixed(0)}%)</span>
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
              <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function TrendChart({ data }: { data: { label: string; cost: number }[] }) {
  if (!data.length) return <Empty title="No usage in this period" />;
  return (
    <div className="h-52">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="label" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={48} tickFormatter={(v: number) => `£${v.toFixed(2)}`} />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
            formatter={(v) => [`£${Number(v).toFixed(4)}`, "Cost"]}
          />
          <Bar dataKey="cost" fill="var(--accent)" radius={[4, 4, 0, 0]} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------- plan settings

export function PlanSettingsTab() {
  const q = useAsync(api.billingSettings.get, []);
  const [draft, setDraft] = useState<BillingSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (q.data) setDraft(q.data);
  }, [q.data]);

  if (q.error) return <ErrorBox error={q.error} onRetry={q.reload} />;
  if (!draft) return <Spinner />;
  const set = <K extends keyof BillingSettings>(k: K, v: BillingSettings[K]) => {
    setSaved(false);
    setDraft({ ...draft, [k]: v });
  };
  const countField = (k: "interaction_limit" | "topup_interactions") => (
    <Input inputMode="numeric" value={String(draft[k])} onChange={(e) => set(k, Math.max(1, Math.round(Number(e.target.value)) || 1))} />
  );
  const penceField = (k: "care_price_pence" | "topup_price_pence" | "cost_warn_pence" | "cost_target_pence" | "cost_critical_pence") => (
    <Input
      inputMode="decimal"
      value={(draft[k] / 100).toFixed(2)}
      onChange={(e) => set(k, Math.max(0, Math.round(Number(e.target.value.replace(",", ".")) * 100) || 0))}
    />
  );
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const { enforce, care_price_pence, topup_price_pence, thresholds, usd_gbp_rate, interaction_limit, topup_interactions,
        cost_warn_pence, cost_target_pence, cost_critical_pence } = draft;
      q.setData(
        await api.billingSettings.update({ enforce, care_price_pence, topup_price_pence, thresholds, usd_gbp_rate, interaction_limit,
          topup_interactions, cost_warn_pence, cost_target_pence, cost_critical_pence }),
      );
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="max-w-3xl space-y-4">
      <Card title="Stripe">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={draft.stripe_mode === "live" ? "danger" : draft.stripe_mode === "test" ? "accent" : "neutral"}>
            {draft.stripe_mode === "off" ? "Not configured" : `${draft.stripe_mode} mode`}
          </Badge>
          <span className="text-muted">
            Keys and price ids live in the server environment. Live charging needs sk_live_ keys — never enabled from here.
          </span>
        </div>
      </Card>
      <Card title="ola Care">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Selling price per month (£, what the customer pays)" hint="Shown to customers. The Stripe price id must match.">
            {penceField("care_price_pence")}
          </Field>
          <Field label="AI interactions per month" hint="The customer's allowance, shared by an account's watches. Enforced.">
            {countField("interaction_limit")}
          </Field>
          <Field label="Extra usage: selling price (£, one-off)">{penceField("topup_price_pence")}</Field>
          <Field label="Extra usage: interactions added" hint="For the rest of the billing period.">
            {countField("topup_interactions")}
          </Field>
          <Field label="Usage notifications (%)" hint="Comma-separated, e.g. 80,95,100. Each shows once per period.">
            <Input value={draft.thresholds} onChange={(e) => set("thresholds", e.target.value)} />
          </Field>
          <Field label="USD → GBP rate for provider costs" hint="Applied to new usage; recorded costs keep the rate of their day.">
            <Input value={draft.usd_gbp_rate} onChange={(e) => set("usd_gbp_rate", e.target.value)} />
          </Field>
        </div>
        <div className="mt-5 rounded-lg border border-border p-3">
          <p className="text-sm font-medium">Internal AI cost monitoring (per account, per month)</p>
          <p className="mt-1 text-xs text-muted">
            Operator alerts only - never shown to customers and never used to refuse a request.
          </p>
          <div className="mt-3 grid gap-4 sm:grid-cols-3">
            <Field label="Early warning (£)">{penceField("cost_warn_pence")}</Field>
            <Field label="Cost target (£)">{penceField("cost_target_pence")}</Field>
            <Field label="Critical (£)">{penceField("cost_critical_pence")}</Field>
          </div>
        </div>
        <div className="mt-5 rounded-lg border border-border p-3">
          <Toggle checked={draft.enforce} onChange={(v) => set("enforce", v)} label="Check subscriptions and interaction allowances" />
          <p className="mt-2 text-xs text-muted">
            {draft.stripe_configured
              ? "Always on while Stripe is configured."
              : "Without Stripe, watches of accounts without a plan are refused once this is on. Internal accounts are never limited."}
          </p>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" loading={busy} onClick={save}>
            Save plan settings
          </Button>
          {saved && <span className="text-sm text-ok">Saved (recorded in the audit log)</span>}
        </div>
        <ErrorBox error={error} />
        <p className="mt-3 text-xs text-muted">
          Last changed {new Date(draft.updated_at).toLocaleString("en-GB")} by {draft.updated_by || "—"}. Proposed prices are not
          commercially approved until you decide so.
        </p>
      </Card>
    </div>
  );
}
