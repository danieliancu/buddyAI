import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { CalendarSync, ChevronDown, CircleCheck, ExternalLink, MessagesSquare, Package, PlusCircle, ShieldCheck, Sparkles } from "lucide-react";
import { api, ApiError, type MyPlan } from "../../api";
import { fmtDate } from "../../format";
import { careErrorText, carePlanStatus, fmtDayMonth, OrderStatusBadge, pence, UsageGauge } from "../../components/BillingBits";
import { Button, Card, ErrorBox, cx, useAsync } from "../../components/ui";
import { useLive } from "../../live";

/** Start a one-off extra-usage purchase (Stripe Checkout). Nothing is added until the payment succeeds. */
export async function startTopup(): Promise<void> {
  const { url } = await api.me.topupCheckout();
  window.location.assign(url);
}

/**
 * "ola Care": plan status, how much of this period's AI usage is used (a share, never internal
 * costs), activity, reset date, fair use, prices, extra usage and orders.
 */
export default function PlanCard() {
  const plan = useAsync(api.me.plan, []);
  const billing = useAsync(api.me.subscription, []); // orders
  const [params, setParams] = useSearchParams();
  const returned = params.get("topup") ?? (params.get("subscribed") ? "subscribed" : null);
  const [waiting, setWaiting] = useState(returned === "success");
  const topupsBefore = useRef<number | null>(null);

  useLive((e) => {
    if (e.type === "turn_end" || e.type === "usage_threshold") plan.reload();
  });

  // Back from Stripe after a top-up: the extra usage appears once the payment webhook arrives.
  useEffect(() => {
    if (!waiting) return;
    let tries = 0;
    const t = window.setInterval(async () => {
      tries += 1;
      const fresh = await api.me.plan().catch(() => null);
      if (fresh) {
        plan.setData(fresh);
        const paid = fresh.topups.filter((x) => x.status === "paid").length;
        if (topupsBefore.current === null) topupsBefore.current = paid - 1;
        if (paid > (topupsBefore.current ?? 0) || tries >= 15) {
          setWaiting(false);
          window.clearInterval(t);
        }
      }
    }, 2000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting]);

  if (plan.error) return <ErrorBox error={plan.error} onRetry={plan.reload} />;
  const p = plan.data;
  if (!p) return null;
  if (!p.billing_enabled && !p.enforced && (p.status.kind === "none" || p.status.kind === "internal")) return null;

  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <Sparkles className="size-4 text-accent" /> ola Care
        </span>
      }
    >
      <div className="space-y-5">
        {returned && <ReturnNotice kind={returned} waiting={waiting} onClose={() => setParams({}, { replace: true })} />}
        <StatusLine plan={p} />
        <CareDetails plan={p} onChange={plan.reload} />
        {p.enforced && (
          <>
            <UsageGauge pct={p.usage.used_pct} />
            <div className="grid grid-cols-2 gap-3">
              <Stat icon={<MessagesSquare className="size-4" />} label="Conversations this period" value={String(p.usage.activity_count)} />
              <Stat icon={<CalendarSync className="size-4" />} label="Usage resets on" value={fmtDayMonth(p.usage.reset_at)} />
            </div>
            {p.usage.extra_pct > 0 && (
              <p className="flex items-center gap-2 text-sm text-ok">
                <CircleCheck className="size-4" /> Extra usage added this period: +{p.usage.extra_pct}% of a month
              </p>
            )}
            <ThresholdHint plan={p} />
          </>
        )}
        <Actions plan={p} />
        <FairUse plan={p} />
        {p.topups.length > 0 && <Topups plan={p} />}
        {billing.data && billing.data.orders.length > 0 && <Orders orders={billing.data.orders} />}
      </div>
    </Card>
  );
}

function StatusLine({ plan }: { plan: MyPlan }) {
  const s = carePlanStatus(plan);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p
        className={cx(
          "font-semibold",
          s.tone === "danger" ? "text-danger" : s.tone === "warn" ? "text-warn" : s.tone === "ok" ? "text-ok" : s.tone === "accent" ? "text-accent" : "",
        )}
      >
        {s.text}
      </p>
      {plan.status.kind !== "complimentary" && plan.status.kind !== "internal" && (
        <span className="text-sm text-muted">{pence(plan.prices.care_price_pence)} / month</span>
      )}
    </div>
  );
}

/** What the trial means in money terms, and the pending / failed states before Stripe confirms. */
export function CareDetails({ plan, onChange }: { plan: MyPlan; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const care = plan.care_activation;
  const price = pence(plan.prices.care_price_pence);

  // Cancelled but still running (until the end of the trial or of the paid month).
  const endsOn = plan.status.kind === "trial" ? plan.status.trial_end : plan.status.period_end;
  if ((plan.status.kind === "trial" || plan.status.kind === "active") && plan.status.cancel_at_period_end && endsOn) {
    return <CareCancelled plan={plan} endsOn={fmtDayMonth(endsOn)} />;
  }
  if (plan.status.kind === "trial" && plan.status.trial_end) {
    const end = fmtDayMonth(plan.status.trial_end);
    return (
      <p className="rounded-lg bg-accent-bg px-3 py-2 text-sm" data-testid="care-trial">
        Free until <b>{end}</b>. Then {price} a month, charged automatically to your saved card until you cancel. Cancel
        before {end} with “Manage billing” to pay nothing.
      </p>
    );
  }
  if (!care || plan.status.kind !== "none") return null;
  if (care.status === "awaiting_pairing") {
    return (
      <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted" data-testid="care-awaiting">
        Your {care.trial_days}-day free trial starts when your watch is paired — nothing is charged before it ends. Then {price} a
        month until you cancel.{" "}
        <Link to="/my/setup" className="font-medium text-accent hover:underline">
          Set up your watch
        </Link>
      </p>
    );
  }
  if (care.status === "activating") {
    return <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm text-muted">Starting your free trial — this takes a few seconds.</p>;
  }
  if (care.status !== "failed") return null;
  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.me.activateCare();
      onChange();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2 rounded-lg bg-warn-bg px-3 py-2.5 text-sm text-warn" data-testid="care-failed">
      <p>Your watch is paired, but your ola Care subscription isn't set up yet. {careErrorText(care.error)}</p>
      <Button variant="secondary" size="sm" loading={busy} onClick={retry}>
        Try again
      </Button>
      <ErrorBox error={error} />
    </div>
  );
}

/** ola Care was cancelled: what still works, until when, what it costs (nothing), and how to keep it. */
function CareCancelled({ plan, endsOn }: { plan: MyPlan; endsOn: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const trial = plan.status.kind === "trial";
  const keep = async () => {
    setBusy(true);
    setError(null);
    try {
      window.location.assign((await api.me.billingPortal()).url);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm" data-testid="care-cancelled">
      <p className="font-semibold text-warn">ola Care is cancelled</p>
      <ul className="space-y-1.5 text-fg">
        <li>
          • Your {trial ? "free trial" : "plan"} still works until <b>{endsOn}</b>.
        </li>
        <li>
          • <b>Nothing more will be charged</b> — {trial ? "the free trial won't turn into a paid plan" : "it won't renew"}.
        </li>
        <li>• After {endsOn} the assistant stops answering. Your notes and reminders stay in your account.</li>
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" className="h-11 px-5 text-base" loading={busy} icon={<ShieldCheck className="size-5" />} onClick={keep}>
          Keep ola Care
        </Button>
        <span className="text-xs text-muted">
          Then {pence(plan.prices.care_price_pence)} a month{trial ? ` from ${endsOn}` : ""}. You can change your mind any time before {endsOn}.
        </span>
      </div>
      <ErrorBox error={error} />
    </div>
  );
}

function Stat({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-xl bg-surface-2 px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-xs text-muted">
        {icon}
        {label}
      </p>
      <p className="tabular mt-0.5 text-lg font-semibold">{value}</p>
    </div>
  );
}

function ThresholdHint({ plan }: { plan: MyPlan }) {
  const pct = plan.usage.used_pct;
  if (pct >= 100)
    return (
      <p className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger">
        This month's AI usage is used up. Ola answers again on {fmtDayMonth(plan.usage.reset_at)}
        {plan.topup_available ? ", or add extra usage now." : "."}
      </p>
    );
  if (pct >= 80)
    return (
      <p className="rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn">
        {pct}% used. It resets on {fmtDayMonth(plan.usage.reset_at)}.
      </p>
    );
  return null;
}

function Actions({ plan }: { plan: MyPlan }) {
  const [busy, setBusy] = useState<"" | "topup" | "subscribe" | "portal">("");
  const [error, setError] = useState<unknown>(null);
  const status = carePlanStatus(plan);

  const run = async (kind: "topup" | "subscribe" | "portal") => {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "topup") await startTopup();
      else {
        const { url } = kind === "subscribe" ? await api.me.subscribe() : await api.me.billingPortal();
        window.location.assign(url);
      }
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? new Error("There is no card on file for this account yet.") : e);
      setBusy("");
    }
  };

  if (!plan.topup_available && !plan.can_subscribe && !plan.can_manage_billing) return null;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {plan.can_subscribe && (
          <Button variant="primary" loading={busy === "subscribe"} icon={<ShieldCheck className="size-4" />} onClick={() => run("subscribe")}>
            Subscribe — {pence(plan.prices.care_price_pence)} / month
          </Button>
        )}
        {plan.topup_available && (
          <Button
            className="w-full justify-center sm:w-auto"
            variant={plan.usage.used_pct >= 95 ? "primary" : "secondary"}
            loading={busy === "topup"}
            icon={<PlusCircle className="size-4" />}
            onClick={() => run("topup")}
          >
            Add extra usage — {pence(plan.prices.topup_price_pence)}
          </Button>
        )}
        {plan.can_manage_billing && (
          <Button variant={status.problem ? "primary" : "ghost"} loading={busy === "portal"} icon={<ExternalLink className="size-4" />} onClick={() => run("portal")}>
            {status.problem ? "Update card" : "Manage billing"}
          </Button>
        )}
      </div>
      {plan.topup_available && (
        <p className="text-xs text-muted">
          One-off payment, never recurring. Adds about {plan.prices.topup_adds_pct}% of a month's usage until{" "}
          {fmtDayMonth(plan.usage.reset_at)}.
        </p>
      )}
      <ErrorBox error={error} />
    </div>
  );
}

function FairUse({ plan }: { plan: MyPlan }) {
  return (
    <details className="group rounded-xl border border-border px-4 py-3 text-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 font-medium">
        How usage works
        <ChevronDown className="size-4 text-muted transition group-open:rotate-180" />
      </summary>
      <div className="mt-3 space-y-2 text-muted">
        <p>
          ola Care includes a monthly amount of AI usage, shared by all your watches. The bar shows how much of it you have used
          this period.
        </p>
        <p>
          <b className="text-fg">Requests use different amounts.</b> A quick question uses a little. Questions that need the internet
          (weather, opening hours, travel), long answers and long recordings use more. So the number of conversations you can have
          varies — it is not a fixed count.
        </p>
        <p>
          Usage resets on {fmtDayMonth(plan.usage.reset_at)}. If it runs out before then, Ola pauses its answers until the reset
          {plan.topup_available || plan.billing_enabled
            ? `, or you can add extra usage for the rest of the period (${pence(plan.prices.topup_price_pence)}, one-off, never recurring). Extra usage ends with the period.`
            : "."}
        </p>
        <p>
          Fair use: ola Care is for personal use with your own watches. It is not unlimited. We tell you at{" "}
          {plan.thresholds.filter((t) => t < 100).join("% and ")}% and when it is used up — we never charge you for more without asking.
          A refunded extra-usage purchase is removed from your allowance. A question that fails because of a fault on our side does
          not count.
        </p>
      </div>
    </details>
  );
}

function Topups({ plan }: { plan: MyPlan }) {
  return (
    <div className="border-t border-border pt-4">
      <p className="mb-2 flex items-center gap-2 text-sm font-medium">
        <PlusCircle className="size-4 text-muted" /> Extra usage purchases
      </p>
      <ul className="space-y-2">
        {plan.topups.map((t) => (
          <li key={t.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-sm">
            <span className="font-medium">{pence(t.amount_pence)}</span>
            <span className="text-muted">{t.paid_at ? fmtDate(t.paid_at) : ""}</span>
            <span className={cx("ml-auto text-xs", t.status === "refunded" ? "text-warn" : t.current ? "text-ok" : "text-muted")}>
              {t.status === "refunded" ? "Refunded" : t.current ? `Active until ${fmtDayMonth(t.period_end)}` : `Ended ${fmtDayMonth(t.period_end)}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Orders({ orders }: { orders: { id: number; status: string; created_at: string; tracking_number: string; carrier: string }[] }) {
  return (
    <div className="border-t border-border pt-4">
      <p className="mb-2 flex items-center gap-2 text-sm font-medium">
        <Package className="size-4 text-muted" /> Orders
      </p>
      <ul className="space-y-2">
        {orders.map((o) => (
          <li key={o.id} className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">Order #{o.id}</span>
              <span className="text-muted">{fmtDate(o.created_at)}</span>
              <span className="ml-auto">
                <OrderStatusBadge status={o.status} />
              </span>
            </div>
            {o.tracking_number && (
              <p className="mt-1 text-xs text-muted">
                Tracking{o.carrier ? ` (${o.carrier})` : ""}: <span className="font-mono text-fg select-all">{o.tracking_number}</span>
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReturnNotice({ kind, waiting, onClose }: { kind: string; waiting: boolean; onClose: () => void }) {
  const text =
    kind === "success"
      ? waiting
        ? "Payment received — adding your extra usage…"
        : "Thank you — your extra usage is ready."
      : kind === "cancel"
        ? "Purchase cancelled. Nothing was charged."
        : "Thank you — your subscription is being set up.";
  return (
    <div className={cx("flex items-center gap-3 rounded-lg px-3 py-2 text-sm", kind === "cancel" ? "bg-surface-2 text-muted" : "bg-ok-bg text-ok")}>
      <CircleCheck className="size-4 shrink-0" />
      <span className="flex-1">{text}</span>
      <button className="text-xs underline" onClick={onClose}>
        Close
      </button>
    </div>
  );
}
