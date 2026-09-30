import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Gauge, PlusCircle, X } from "lucide-react";
import { api, type MyPlan, type UsageNotice as Notice } from "../api";
import { useLive } from "../live";
import { startTopup } from "../pages/my/PlanCard";
import { fmtDayMonth, pence } from "./BillingBits";
import { Button, Dialog, ErrorBox, cx } from "./ui";

/**
 * Usage thresholds (80 / 95 / 100 % of the period's allowance), each shown once per period until
 * dismissed: a slim banner at 80 % (information) and 95 % (warning), a dialog at 100 % offering extra
 * usage. Loaded on start and when the server reports a newly crossed threshold - never after every
 * conversation.
 */
export default function UsageNotice() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [plan, setPlan] = useState<MyPlan | null>(null);

  const load = async () => {
    try {
      const { notice: n } = await api.me.usageNotice();
      setNotice(n);
      setPlan(n ? await api.me.plan() : null);
    } catch {
      /* not important enough to show an error */
    }
  };

  useEffect(() => {
    void load();
  }, []);
  useLive((e) => {
    if (e.type === "usage_threshold") void load();
  });

  if (!notice || !plan) return null;
  const dismiss = async () => {
    setNotice(null);
    await api.me.dismissUsageNotice(notice.threshold).catch(() => undefined);
  };

  if (notice.level === "limit") return <LimitDialog plan={plan} onClose={dismiss} />;
  const warn = notice.level === "warning";
  return (
    <div className={cx("border-b", warn ? "border-warn/30 bg-warn-bg" : "border-accent/30 bg-accent-bg")}>
      <div className={cx("mx-auto flex max-w-5xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 text-sm sm:px-6", warn ? "text-warn" : "text-accent")}>
        <Gauge className="size-4 shrink-0" />
        <p className="min-w-0 flex-1 basis-56">
          You've used {notice.threshold}% of this month's AI usage. It resets on {fmtDayMonth(plan.usage.reset_at)}.
        </p>
        <div className="flex gap-2">
          {warn && plan.topup_available ? (
            <TopupButton plan={plan} size="sm" />
          ) : (
            <Link to="/my/account" className="rounded-lg px-2 py-1 text-sm font-medium underline-offset-2 hover:underline">
              See usage
            </Link>
          )}
          <button onClick={() => void dismiss()} aria-label="Dismiss" className="rounded-lg p-1.5 hover:bg-surface-2/60">
            <X className="size-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

function TopupButton({ plan, size = "md" }: { plan: MyPlan; size?: "sm" | "md" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <>
      <Button
        size={size}
        variant="primary"
        loading={busy}
        icon={<PlusCircle className="size-4" />}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await startTopup();
          } catch (e) {
            setError(e);
            setBusy(false);
          }
        }}
      >
        Add extra usage — {pence(plan.prices.topup_price_pence)}
      </Button>
      {error != null && <ErrorBox error={error} />}
    </>
  );
}

function LimitDialog({ plan, onClose }: { plan: MyPlan; onClose: () => void }) {
  return (
    <Dialog
      open
      onClose={onClose}
      title="Monthly AI usage reached"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Not now
          </Button>
          {plan.topup_available ? (
            <TopupButton plan={plan} />
          ) : (
            <Link to="/my/account" onClick={onClose} className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-accent-fg">
              See my plan
            </Link>
          )}
        </>
      }
    >
      <div className="space-y-3 text-sm">
        <p>
          You've used all of this month's AI usage. Ola will answer again on <b>{fmtDayMonth(plan.usage.reset_at)}</b>.
        </p>
        {plan.topup_available && (
          <p className="text-muted">
            Want to keep talking before then? Add extra usage for this period: {pence(plan.prices.topup_price_pence)}, one-off, never
            recurring.
          </p>
        )}
      </div>
    </Dialog>
  );
}
