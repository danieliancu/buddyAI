import { useState } from "react";
import { CreditCard, ExternalLink, Package } from "lucide-react";
import { api, ApiError } from "../../api";
import { fmtDate } from "../../format";
import { Meter, OrderStatusBadge, planStatus } from "../../components/BillingBits";
import { Button, Card, ErrorBox, cx, useAsync } from "../../components/ui";

/** "Plan" section: subscription status, allowance meter, orders, Stripe billing portal. Hidden when billing is off. */
export default function PlanCard() {
  const sub = useAsync(api.me.subscription, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [noPortal, setNoPortal] = useState(false);

  if (sub.error) return <ErrorBox error={sub.error} onRetry={sub.reload} />;
  const d = sub.data;
  if (!d || !d.billing_enabled) return null;

  const plan = planStatus(d.subscription);
  const openPortal = async () => {
    setBusy(true);
    setError(null);
    try {
      const { url } = await api.me.billingPortal();
      window.location.assign(url);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setNoPortal(true);
      else setError(e);
      setBusy(false);
    }
  };

  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <CreditCard className="size-4 text-accent" /> Plan
        </span>
      }
    >
      <div className="space-y-4">
        <div>
          <p className="text-xs text-muted">BuddyAI Care</p>
          <p
            className={cx(
              "font-semibold",
              plan.tone === "danger" ? "text-danger" : plan.tone === "warn" ? "text-warn" : plan.tone === "ok" ? "text-ok" : "",
            )}
          >
            {plan.text}
          </p>
        </div>
        {d.subscription && <Meter pct={d.allowance_used_pct} label="Monthly allowance used" />}
        {d.subscription && d.allowance_used_pct >= 100 && (
          <p className="text-sm text-warn">This month's allowance is used up. Buddy answers again from the 1st of next month.</p>
        )}
        {d.subscription && !noPortal && (
          <Button variant={plan.problem ? "primary" : "secondary"} loading={busy} icon={<ExternalLink className="size-4" />} onClick={openPortal}>
            {plan.problem ? "Update card" : "Manage subscription"}
          </Button>
        )}
        <ErrorBox error={error} />

        {d.orders.length > 0 && (
          <div className="border-t border-border pt-4">
            <p className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Package className="size-4 text-muted" /> Orders
            </p>
            <ul className="space-y-2">
              {d.orders.map((o) => (
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
        )}
      </div>
    </Card>
  );
}
