import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, Ban, CheckCircle2, ScrollText, Watch } from "lucide-react";
import { api, ApiError, type AccountCost, type AccountDetail, type Order } from "../api";
import { fmtAgo, fmtDateTime } from "../format";
import { fmtDayMonth, Meter, OrderStatusBadge, planStatus } from "../components/BillingBits";
import { fmtMinor, OrderDialog } from "./OrdersPage";
import { BatteryInfo, OnlineDot, StateBadge } from "../components/DeviceBits";
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorBox, Field, Input, PageHeader, Spinner, Table, useAsync } from "../components/ui";
import { AccountStatusBadge } from "./CustomersPage";

const ACTION_LABEL: Record<string, string> = {
  "account.create": "Account created",
  "account.active": "Reactivated",
  "account.suspended": "Suspended",
  "account.allowance": "Interaction allowance changed",
  "cost.alert": "AI cost alert (internal)",
  "subscription.complimentary": "Complimentary plan granted",
  "subscription.complimentary.extend": "Complimentary plan extended",
  "subscription.complimentary.revoke": "Complimentary plan ended",
  "topup.checkout": "Extra usage checkout started",
  "topup.paid": "Extra usage paid",
  "topup.refunded": "Extra usage refunded",
  "order.shipped": "Order shipped",
  "order.delivered": "Order delivered",
  "order.cancelled": "Order cancelled",
  "order.paid": "Order marked paid",
  "device.pair": "Watch paired",
  "device.assign": "Watch assigned",
  "device.revoke": "Watch revoked",
  "history.view": "History viewed",
  "history.delete": "History deleted",
};

export default function CustomerDetailPage() {
  const { id: idParam = "" } = useParams();
  const id = Number(idParam);
  const acc = useAsync(() => api.accounts.get(id), [id]);
  const audit = useAsync(() => api.accounts.audit(id), [id]);
  const [confirm, setConfirm] = useState(false);
  const [editingOrder, setEditingOrder] = useState<Order | null>(null);

  const back = (
    <Link to="/admin/customers" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
      <ArrowLeft className="size-4" /> Customers
    </Link>
  );

  if (acc.error) {
    return (
      <>
        {back}
        <ErrorBox error={acc.error instanceof ApiError && acc.error.status === 404 ? new Error("Account not found.") : acc.error} onRetry={acc.reload} />
      </>
    );
  }
  if (!acc.data) return <Spinner />;
  const a = acc.data;
  const suspended = a.status === "suspended";

  return (
    <>
      {back}
      <PageHeader
        title={a.name || a.email}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            <AccountStatusBadge status={a.status} />
            {a.email_verified ? <Badge tone="ok">Email verified</Badge> : <Badge tone="warn">Email not verified</Badge>}
            {!a.has_password && <Badge tone="warn">Password not set</Badge>}
          </span>
        }
        actions={
          suspended ? (
            <Button variant="primary" icon={<CheckCircle2 className="size-4" />} onClick={() => setConfirm(true)}>
              Reactivate
            </Button>
          ) : (
            <Button variant="danger" icon={<Ban className="size-4" />} onClick={() => setConfirm(true)}>
              Suspend
            </Button>
          )
        }
      />

      <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
        <Card title="Profile">
          <dl className="space-y-3 text-sm">
            <Row label="Email">
              <span className="break-all">{a.email}</span>
            </Row>
            <Row label="Name">{a.name || "—"}</Row>
            <Row label="Country">{a.country ?? "—"}</Row>
            <Row label="Account id">
              <span className="font-mono">#{a.id}</span>
            </Row>
            <Row label="Created">{fmtDateTime(a.created_at)}</Row>
            <Row label="Last login">{a.last_login_at ? `${fmtDateTime(a.last_login_at)} (${fmtAgo(a.last_login_at)})` : "never"}</Row>
          </dl>
        </Card>
        <PlanCard account={a} onChanged={() => { acc.reload(); audit.reload(); }} />
        </div>

        <div className="min-w-0 space-y-6">
          <Card title={`Watches (${a.devices.length})`} bodyClassName="p-0">
            {a.devices.length === 0 ? (
              <Empty icon={<Watch className="size-7" />} title="No watches">
                Assign a watch from the Devices page, or the customer pairs one from their account.
              </Empty>
            ) : (
              <ul className="divide-y divide-border">
                {a.devices.map((d) => (
                  <li key={d.id}>
                    <Link to={`/admin/devices/${encodeURIComponent(d.id)}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-surface-2/50">
                      <OnlineDot online={d.online} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{d.name}</span>
                        <span className="block truncate font-mono text-xs text-muted">{d.id}</span>
                      </span>
                      <StateBadge online={d.online} state={d.state} />
                      <BatteryInfo pct={d.battery_pct} charging={d.charging} />
                      <span className="text-xs text-muted">{d.online ? "online" : `seen ${fmtAgo(d.last_seen_at)}`}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title={`Orders (${a.orders?.length ?? 0})`} bodyClassName={a.orders?.length ? "p-0 px-4" : undefined}>
            {!a.orders?.length ? (
              <p className="text-sm text-muted">No orders.</p>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Date</th>
                    <th className="text-right">Amount</th>
                    <th>Status</th>
                    <th>Tracking</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {a.orders.map((o) => (
                    <tr key={o.id}>
                      <td className="tabular">{o.id}</td>
                      <td className="whitespace-nowrap">{fmtDateTime(o.created_at)}</td>
                      <td className="tabular text-right">{fmtMinor(o.amount_total, o.currency)}</td>
                      <td>
                        <OrderStatusBadge status={o.status} operator />
                      </td>
                      <td className="font-mono text-xs">{o.tracking_number || "—"}</td>
                      <td className="text-right">
                        {o.status !== "refunded" && (
                          <Button size="sm" onClick={() => setEditingOrder(o)}>
                            {o.status === "paid" ? "Mark shipped" : "Edit"}
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>

          <Card title={<span className="inline-flex items-center gap-2"><ScrollText className="size-4 text-accent" /> Audit log</span>}>
            <p className="mb-3 text-xs text-muted">Operator access to this customer's data (GDPR accountability). Latest 200 entries.</p>
            <ErrorBox error={audit.error} onRetry={audit.reload} />
            {audit.loading && !audit.data ? (
              <Spinner />
            ) : !audit.data?.length ? (
              <p className="text-sm text-muted">No entries.</p>
            ) : (
              <Table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Who</th>
                    <th>Action</th>
                    <th>Watch</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.data.map((e) => (
                    <tr key={e.id}>
                      <td className="whitespace-nowrap">{fmtDateTime(e.created_at)}</td>
                      <td>{e.actor}</td>
                      <td>{ACTION_LABEL[e.action] ?? e.action}</td>
                      <td className="font-mono text-xs">{e.device_id ?? "—"}</td>
                      <td className="max-w-72 truncate text-muted" title={e.detail}>
                        {e.detail || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </Card>
        </div>
      </div>

      <OrderDialog
        order={editingOrder}
        onClose={() => setEditingOrder(null)}
        onSaved={() => {
          acc.reload();
          audit.reload();
        }}
      />
      <ConfirmDialog
        open={confirm}
        danger={!suspended}
        title={suspended ? "Reactivate account" : "Suspend account"}
        confirmLabel={suspended ? "Reactivate" : "Suspend"}
        message={
          suspended ? (
            <>
              <b>{a.email}</b> can sign in again and their watches reconnect.
            </>
          ) : (
            <>
              <b>{a.email}</b> is signed out everywhere and can't sign in. Their watches are disconnected until the account is
              reactivated. No data is deleted.
            </>
          )
        }
        onConfirm={async () => {
          await api.accounts.setStatus(a.id, suspended ? "active" : "suspended");
          acc.reload();
          audit.reload();
        }}
        onClose={() => setConfirm(false)}
      />
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-2">
      <dt className="text-muted">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Subscription status + monthly allowance with an operator override. */
/** Complimentary / test access: no payment, no Stripe objects; recorded in the audit log. */
function ComplimentaryControls({ account: a, onChanged }: { account: AccountDetail; onChanged: () => void }) {
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const sub = a.subscription;
  const stripePaid = sub?.source === "stripe" && a.entitled;
  const activeComp = sub?.source === "complimentary" && a.entitled;
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    try {
      setMessage(await fn());
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  if (stripePaid) return null;
  const n = Math.max(1, Math.min(366, Number(days) || 30));
  return (
    <div className="mt-4 space-y-2 border-t border-border pt-4">
      <p className="text-xs font-medium text-muted">Complimentary pilot (no payment)</p>
      <div className="flex flex-wrap items-center gap-2">
        <Input inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} className="w-20" aria-label="Days" />
        <span className="text-sm text-muted">days</span>
        {activeComp ? (
          <>
            <Button size="sm" loading={busy} onClick={() => run(async () => ((await api.accounts.grantComplimentary(a.id, { days: n, extend: true })).created ? `Extended by ${n} days.` : "Unchanged."))}>
              Extend
            </Button>
            <Button size="sm" variant="ghost" loading={busy} onClick={() => run(async () => ((await api.accounts.revokeComplimentary(a.id)).revoked ? "Complimentary access ended." : "Nothing to end."))}>
              End now
            </Button>
          </>
        ) : (
          <Button size="sm" variant="primary" loading={busy} onClick={() => run(async () => ((await api.accounts.grantComplimentary(a.id, { days: n })).created ? `Granted for ${n} days.` : "Already granted — unchanged."))}>
            Grant olá Care
          </Button>
        )}
      </div>
      {message && <p className="text-xs text-ok">{message}</p>}
      <ErrorBox error={error} />
    </div>
  );
}

function PlanCard({ account: a, onChanged }: { account: AccountDetail; onChanged: () => void }) {
  const al = a.allowance;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => setValue(al?.override != null ? String(al.override) : ""), [al?.override]);

  if (!al) return null;
  const sub = a.subscription;
  const comp = sub?.source === "complimentary";
  const plan = comp
    ? a.entitled
      ? { text: `Complimentary until ${fmtDayMonth(sub?.current_period_end)}`, tone: "accent" as const }
      : { text: `Complimentary access ended ${fmtDayMonth(sub?.current_period_end)}`, tone: "warn" as const }
    : planStatus(sub);
  const pct = al.limit > 0 ? (al.used / al.limit) * 100 : 0;

  const save = async (override: number | null) => {
    setBusy(true);
    setError(null);
    try {
      await api.accounts.setAllowance(a.id, override);
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = value.trim();
    if (v === "") return void save(null);
    const n = Number(v);
    if (!Number.isInteger(n) || n < 1) return setError(new Error("Enter a whole number of interactions, or leave empty for the plan default."));
    void save(n);
  };

  return (
    <Card title="Plan & allowance">
      <dl className="space-y-3 text-sm">
        <Row label="Plan">
          <Badge tone={plan.tone}>{a.internal ? "internal" : comp ? "complimentary" : sub ? sub.status : "none"}</Badge>
          <span className="mt-1 block text-muted">{a.internal ? "Internal account — never limited, costs still tracked" : plan.text}</span>
          {comp && sub?.note && <span className="mt-1 block text-xs text-muted">{sub.note} · granted by {sub.granted_by ?? "—"}</span>}
        </Row>
        {sub?.trial_end && <Row label="Trial end">{fmtDayMonth(sub.trial_end)}</Row>}
        {sub?.current_period_end && <Row label="Period end">{fmtDayMonth(sub.current_period_end)}</Row>}
        {sub?.stripe_customer_id && (
          <Row label="Stripe">
            <span className="font-mono text-xs break-all">{sub.stripe_customer_id}</span>
          </Row>
        )}
      </dl>
      <div className="mt-4 border-t border-border pt-4">
        <Meter
          pct={pct}
          label={`AI interactions this period (${fmtDayMonth(al.period_start)} – ${fmtDayMonth(al.period_end)}): ${al.used.toLocaleString("en-GB")} of ${al.limit.toLocaleString("en-GB")}${al.extra ? ` (incl. ${al.extra} extra)` : ""}`}
        />
        {a.cost && <CostLine cost={a.cost} />}
        {!a.internal && <ComplimentaryControls account={a} onChanged={onChanged} />}
        <form onSubmit={submit} className="mt-4 space-y-2">
          <Field
            label="Interactions per month (override)"
            htmlFor="al-override"
            hint={al.override != null ? "An authorised exception to the plan's allowance for this account." : "Empty = plan default."}
          >
            <div className="flex gap-2">
              <Input
                id="al-override"
                inputMode="numeric"
                placeholder="Plan default"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className="min-w-0 flex-1"
              />
              <Button type="submit" loading={busy}>
                Save
              </Button>
            </div>
          </Field>
          {al.override != null && (
            <button type="button" className="text-xs text-accent hover:underline" onClick={() => void save(null)} disabled={busy}>
              Reset to plan default
            </button>
          )}
          <ErrorBox error={error} />
        </form>
      </div>
    </Card>
  );
}

const COST_TONE = { within: "ok", approaching: "warn", over: "danger", critical: "danger" } as const;
const COST_TEXT = { within: "Within target", approaching: "Approaching target", over: "Over target", critical: "Critical cost" } as const;

/** Internal AI cost of this account's current period (operator only; monitoring, never enforced). */
function CostLine({ cost: c }: { cost: AccountCost }) {
  const money = (v: number | null) => (v == null ? "—" : `£${v.toFixed(v < 0.1 ? 4 : 2)}`);
  return (
    <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={COST_TONE[c.status]}>{COST_TEXT[c.status]}</Badge>
        <span>
          AI cost {money(c.cost)} (target {money(c.target)}) · {money(c.average_cost)} per interaction ·{" "}
          {c.projected_cost == null ? "no projection yet" : `~${money(c.projected_cost)} projected at the allowance (estimate)`}
        </span>
      </div>
      <p className="mt-1 text-muted">
        Interactive {money(c.interactive_cost)} · background {money(c.background_cost)}
        {c.unpriced_rows > 0 ? ` · ${c.unpriced_rows} unpriced usage rows (the real cost is higher)` : ""}. Internal monitoring only: it never
        limits the customer.
      </p>
    </div>
  );
}
