import type { OrderStatus, SubscriptionInfo, TurnRefusedCode } from "../api";
import { parseDate } from "../format";
import { Badge, cx } from "./ui";

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const dayMonth = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
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

const ORDER_STATUS: Record<string, { label: string; tone: Tone }> = {
  paid: { label: "Paid — preparing", tone: "accent" },
  shipped: { label: "Shipped", tone: "ok" },
  delivered: { label: "Delivered", tone: "neutral" },
  refunded: { label: "Refunded", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function OrderStatusBadge({ status, operator }: { status: OrderStatus; operator?: boolean }) {
  const s = ORDER_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={s.tone}>{operator && status === "paid" ? "Paid" : s.label}</Badge>;
}

/** Why the watch refused a question (live event `turn_refused`). */
export function turnRefusedText(code: TurnRefusedCode, operator = false): string {
  switch (code) {
    case "subscription_required":
      return operator ? "Refused: no active subscription" : "Buddy needs an active BuddyAI Care subscription to answer.";
    case "limit_reached":
      return operator ? "Refused: monthly allowance used up" : "This month's allowance is used up. Buddy will answer again next month.";
    case "account_inactive":
      return operator ? "Refused: account inactive" : "Your account is inactive — please contact support.";
    default:
      return operator ? `Refused: ${code}` : "Buddy couldn't answer the last question.";
  }
}

/** Horizontal meter 0–100 %. */
export function Meter({ pct, label }: { pct: number; label: string }) {
  const v = Math.max(0, Math.min(100, pct));
  const tone = v >= 100 ? "bg-danger" : v >= 80 ? "bg-warn" : "bg-accent";
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2 text-sm">
        <span className="text-muted">{label}</span>
        <span className="tabular font-medium">{Math.round(v)}%</span>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-surface-2"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(v)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={cx("h-full rounded-full transition-all", tone)} style={{ width: `${v}%` }} />
      </div>
    </div>
  );
}
