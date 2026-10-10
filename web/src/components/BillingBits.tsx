import type { MyPlan, OrderStatus, SubscriptionInfo, TurnRefusedCode } from "../api";
import { parseDate } from "../format";
import { Badge, cx } from "./ui";

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const dayMonth = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const longDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const count = new Intl.NumberFormat("en-GB");

/** "8 November 2026". */
export function fmtLongDate(s: string | null | undefined): string {
  const d = parseDate(s);
  return d ? longDate.format(d) : "—";
}

/** 1,000 */
export const fmtCount = (n: number) => count.format(n);
const dayMonthYear = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** "28 Dec" (this year) or "28 Dec 2027". */
export function fmtDayMonth(s: string | null | undefined): string {
  const d = parseDate(s);
  if (!d) return "—";
  return d.getFullYear() === new Date().getFullYear() ? dayMonth.format(d) : dayMonthYear.format(d);
}

/** Subscription status in plain English. */
export function planStatus(sub: SubscriptionInfo | null): { text: string; tone: Tone; problem: boolean } {
  if (!sub) return { text: "No subscription", tone: "neutral", problem: false };
  switch (sub.status) {
    case "trialing":
      return {
        text: sub.cancel_at_period_end ? `Free trial — ends ${fmtDayMonth(sub.trial_end)}` : `Free trial until ${fmtDayMonth(sub.trial_end)}`,
        tone: "accent",
        problem: false,
      };
    case "active":
      return {
        text: sub.cancel_at_period_end
          ? `Active — ends ${fmtDayMonth(sub.current_period_end)}`
          : `Active — renews ${fmtDayMonth(sub.current_period_end)}`,
        tone: "ok",
        problem: false,
      };
    case "past_due":
    case "unpaid":
      return { text: "Payment problem — update your card", tone: "danger", problem: true };
    case "incomplete":
      return { text: "Payment not completed", tone: "warn", problem: true };
    case "canceled":
    case "incomplete_expired":
      return { text: "Cancelled", tone: "neutral", problem: false };
    default:
      return { text: sub.status, tone: "neutral", problem: false };
  }
}

/** 799 -> "£7.99". */
export function pence(p: number): string {
  return `£${(p / 100).toFixed(2)}`;
}

/** ola Care state for the customer (GET /api/me/plan). */
export function carePlanStatus(plan: MyPlan): { text: string; tone: Tone; problem: boolean } {
  const s = plan.status;
  switch (s.kind) {
    case "trial":
      return s.cancel_at_period_end
        ? { text: `Cancelled — free trial until ${fmtDayMonth(s.trial_end)}`, tone: "warn", problem: false }
        : { text: `Free trial until ${fmtDayMonth(s.trial_end)}`, tone: "accent", problem: false };
    case "active":
      return s.cancel_at_period_end
        ? { text: `Cancelled — works until ${fmtDayMonth(s.period_end)}`, tone: "warn", problem: false }
        : { text: `Active — renews ${fmtDayMonth(s.period_end)}`, tone: "ok", problem: false };
    case "past_due":
      return { text: "Payment problem — update your card", tone: "danger", problem: true };
    case "complimentary":
      return { text: `Complimentary pilot — until ${fmtDayMonth(s.period_end)}`, tone: "accent", problem: false };
    case "expired":
      return { text: `Pilot ended on ${fmtDayMonth(s.period_end)}`, tone: "warn", problem: true };
    case "canceled":
      return { text: s.period_end ? `Ended on ${fmtDayMonth(s.period_end)}` : "Cancelled", tone: "neutral", problem: false };
    case "internal":
      return { text: "Internal account — no usage limit", tone: "neutral", problem: false };
    default: {
      const care = careSetupStatus(plan.care_activation);
      return care ?? { text: "No plan yet", tone: "neutral", problem: false };
    }
  }
}

/** Before Stripe confirms a subscription: the trial waiting for the watch, being set up, or stuck. */
export function careSetupStatus(care: MyPlan["care_activation"]): { text: string; tone: Tone; problem: boolean } | null {
  if (!care) return null;
  switch (care.status) {
    case "awaiting_pairing":
      return { text: `Free ${care.trial_days}-day trial — starts when you pair your watch`, tone: "accent", problem: false };
    case "activating":
      return { text: "Starting your free trial…", tone: "accent", problem: false };
    case "failed":
      return { text: "Subscription setup pending", tone: "warn", problem: true };
    default:
      return null; // active: the subscription status shows; not_eligible: no trial
  }
}

/** Why starting ola Care failed, in plain words. */
export function careErrorText(error: string | null): string {
  switch (error) {
    case "missing_payment_method":
      return "We couldn't find the card you saved at checkout. Add a card with “Manage billing”, then try again.";
    case "card_error":
    case "payment_method_invalid":
      return "Your bank didn't accept the saved card for the subscription. Update your card with “Manage billing”, then try again.";
    default:
      return "We couldn't reach our payment provider. Your watch works and is paired; try again in a moment.";
  }
}

/**
 * The account's monthly AI interactions (My Account → ola Care): % used, a bar, used / allowance, remaining and the
 * renewal date - shared by all the account's watches. A subtle note at 80 %, a stronger one at 95 %, a friendly
 * explanation at 100 %. Never costs or tokens.
 */
export function MonthlyUsage({ usage }: { usage: MyPlan["usage"] }) {
  const pct = Math.max(0, Math.min(100, usage.used_pct));
  const level = pct >= 100 ? "limit" : pct >= 95 ? "high" : pct >= 80 ? "warn" : "ok";
  const renews = fmtLongDate(usage.reset_at);
  return (
    <section aria-label="Monthly usage" data-testid="monthly-usage" className="space-y-2.5">
      <div className="flex items-end justify-between gap-3">
        <h3 className="text-sm font-medium">Monthly usage</h3>
        <span className={cx("tabular text-sm font-semibold", level === "limit" ? "text-danger" : level === "ok" ? "text-fg" : "text-warn")}>
          {Math.floor(pct)}% used
        </span>
      </div>
      <div className="liquid" role="progressbar" aria-label="Monthly AI interactions used" aria-valuenow={Math.floor(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={cx("liquid-fill", level === "limit" ? "danger" : level === "ok" ? "" : "warn")} style={{ width: `${pct}%` }} />
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
        <span>
          <b className="tabular">{fmtCount(usage.used)}</b> of {fmtCount(usage.limit)} interactions
        </span>
        <span className="text-muted">
          <span className="tabular">{fmtCount(usage.remaining)}</span> {usage.remaining === 1 ? "interaction" : "interactions"} remaining
        </span>
      </div>
      <p className="text-xs text-muted">
        Renews on {renews}
        {usage.extra > 0 ? ` · includes ${fmtCount(usage.extra)} extra this period` : ""}
      </p>
      {level === "warn" && (
        <p className="text-xs text-warn" data-testid="usage-hint">
          You've used {Math.floor(pct)}% of your monthly AI interactions.
        </p>
      )}
      {level === "high" && (
        <p className="rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn" data-testid="usage-hint">
          You've used {Math.floor(pct)}% of your monthly AI interactions — {fmtCount(usage.remaining)} left until {renews}.
        </p>
      )}
      {level === "limit" && (
        <p className="rounded-lg bg-danger-bg px-3 py-2 text-sm text-danger" data-testid="usage-hint">
          You've reached your {fmtCount(usage.limit)} monthly AI interactions. Your allowance renews on {renews}, when Olá answers
          again. Your saved notes, reminders and their alarms keep working.
        </p>
      )}
    </section>
  );
}

const ORDER_STATUS: Record<string, { label: string; tone: Tone }> = {
  paid: { label: "Paid — preparing", tone: "accent" },
  shipped: { label: "Shipped", tone: "ok" },
  delivered: { label: "Delivered", tone: "neutral" },
  refunded: { label: "Refunded", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  payment_pending: { label: "Payment processing", tone: "warn" },
  payment_failed: { label: "Payment failed", tone: "danger" },
};

export function OrderStatusBadge({ status, operator }: { status: OrderStatus; operator?: boolean }) {
  const s = ORDER_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={s.tone}>{operator && status === "paid" ? "Paid" : s.label}</Badge>;
}

/** Why the watch refused a question (live event `turn_refused`). */
export function turnRefusedText(code: TurnRefusedCode, operator = false): string {
  switch (code) {
    case "subscription_required":
      return operator ? "Refused: no active subscription" : "Olá needs an active olá Care subscription to answer.";
    case "limit_reached":
      return operator
        ? "Refused: monthly AI interactions used up"
        : "You've used all your monthly AI interactions. Olá answers again when your allowance renews — see Account → olá Care.";
    case "account_inactive":
      return operator ? "Refused: account inactive" : "Your account is inactive — please contact support.";
    default:
      return operator ? `Refused: ${code}` : "Olá couldn't answer the last question.";
  }
}

/** Horizontal meter 0–100 %. */
export function Meter({ pct, label }: { pct: number; label: string }) {
  const v = Math.max(0, Math.min(100, pct));
  const tone = v >= 100 ? "danger" : v >= 80 ? "warn" : "";
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-sm">
        <span className="text-muted">{label}</span>
        <span className="tabular font-medium">{Math.round(v)}%</span>
      </div>
      <div
        className="liquid"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(v)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={cx("liquid-fill", tone)} style={{ width: `${v}%` }} />
      </div>
    </div>
  );
}
