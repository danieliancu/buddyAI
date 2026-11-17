import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ChevronRight, CircleCheck, Circle, LifeBuoy, MailWarning, Mic, PackageSearch, Sparkles, Watch, Wifi } from "lucide-react";
import { api, type CareActivation, type MyPlan, type Onboarding } from "../../../api";
import { careErrorText, fmtDayMonth, pence } from "../../../components/BillingBits";
import { Button, Card, ErrorBox, PageHeader, Spinner, buttonCls, cx, useAsync } from "../../../components/ui";
import PairCodeForm from "../PairCodeForm";
import { useCustomer } from "../session";
import { SITE_URL, SUPPORT_EMAIL } from "../../../site";

/**
 * /my/setup: from a paid order to a working watch. Wi-Fi is set on the watch itself (network list + password on
 * its keyboard); the watch then shows a 6-digit code, typed here to pair it → olá Care trial. Progress comes from
 * the server (order, password, email, paired watches, trial), so a refresh or a later sign-in continues where the
 * customer left off.
 */
export default function SetupPage() {
  const { account } = useCustomer();
  const onboarding = useAsync(api.me.onboarding, []);
  const plan = useAsync(api.me.plan, []);
  // "Add watch" (?another=1): the whole flow again for one more watch, even if the account has watches.
  const [params] = useSearchParams();
  const another = params.get("another") === "1";
  const navigate = useNavigate();

  // A watch this account just removed may come back online with a code: keep the page up to date.
  const ob0 = onboarding.data;
  const waiting = !!ob0 && (ob0.watches === 0 || another);
  useEffect(() => {
    if (!waiting) return;
    const t = window.setInterval(() => {
      api.me.onboarding().then(onboarding.setData).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waiting]);

  if (onboarding.error) return <ErrorBox error={onboarding.error} onRetry={onboarding.reload} />;
  const ob = onboarding.data;
  if (!ob) return <Spinner />;

  if (!ob.eligible) return <NoOrder />;
  const paymentPending = ob.order?.status === "payment_pending";
  const paired = ob.watches > 0 && !another;
  const onPaired = (id: string) =>
    another ? navigate(`/my/watch/${encodeURIComponent(id)}?paired=1`, { replace: true }) : (onboarding.reload(), plan.reload());

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <PageHeader title="Set up your olá watch" subtitle="On Wi-Fi, you're on — about two minutes. Keep the watch charged." />

      <Progress ob={ob} />

      {paymentPending ? (
        <Notice tone="warn">
          Your bank is still confirming the payment. We'll email you as soon as it's confirmed; you can set up the watch after that.
        </Notice>
      ) : paired ? (
        <Done ob={ob} plan={plan.data} onPlanChange={() => (onboarding.reload(), plan.reload())} onFinish={() => navigate("/my")} />
      ) : (
        <>
          {ob.waiting_watch ? (
            <Notice tone="ok" icon={<Watch className="size-4" />}>
              <span data-testid="waiting-watch">Your watch “{ob.waiting_watch.name}” is online. Enter the 6-digit code it shows.</span>
            </Notice>
          ) : (
            <ThreeSteps />
          )}
          <Section icon={<Watch className="size-4" />} title="Type the code shown on the watch" done={false}>
            {!ob.email_verified ? (
              <Notice tone="warn" icon={<MailWarning className="size-4" />}>
                Confirm your email address first: use the link we sent to <b className="break-all">{account.email}</b>, or “Resend
                email” above.
              </Notice>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-muted">The code is valid for 5 minutes — after that the watch shows a new one by itself.</p>
                <PairCodeForm onPaired={onPaired} />
              </div>
            )}
          </Section>
          <Section icon={<Sparkles className="size-4" />} title="olá Care free trial" done={false} muted>
            <CareSummary care={ob.care} plan={plan.data} />
          </Section>
          <Troubleshooting />
        </>
      )}
    </div>
  );
}

/** The whole setup in three steps; the first one happens on the watch itself. */
function ThreeSteps() {
  const [help, setHelp] = useState(false);
  const steps: [ReactNode, string, string][] = [
    [<Wifi className="size-4" />, "On the watch", "Choose your Wi-Fi network and type its password."],
    [<Watch className="size-4" />, "Type the code", "The watch shows a 6-digit code: type it below."],
    [<CircleCheck className="size-4" />, "Done", "Tap the microphone and say “Make a note to buy milk.”"],
  ];
  return (
    <Card>
      <ol className="grid gap-3 sm:grid-cols-3" data-testid="setup-steps">
        {steps.map(([icon, title, text], i) => (
          <li key={title} className="flex items-start gap-3">
            <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-accent">{icon}</span>
            <span className="text-sm">
              <span className="block font-semibold">
                {i + 1}. {title}
              </span>
              <span className="block text-muted">{text}</span>
            </span>
          </li>
        ))}
      </ol>
      <button
        type="button"
        className="mt-3 cursor-pointer text-sm text-accent hover:underline"
        onClick={() => setHelp((v) => !v)}
        aria-expanded={help}
      >
        No code on the watch?
      </button>
      {help && (
        <div className="mt-2 space-y-2 rounded-xl bg-surface-2 px-4 py-3 text-sm text-muted" data-testid="setup-help">
          <p>
            <b className="text-fg">A list of Wi-Fi networks:</b> tap your home network and type its password on the watch. The watch
            needs a 2.4 GHz network — most home routers have one.
          </p>
          <p>
            <b className="text-fg">The clock:</b> the watch is already set up. To use another network, open Settings → Wi-Fi setup on the
            watch.
          </p>
          <p>
            <b className="text-fg">Something else:</b> see Troubleshooting at the bottom of this page.
          </p>
        </div>
      )}
    </Card>
  );
}

/** Rare problems, out of the way of the normal setup. */
function Troubleshooting() {
  return (
    <details className={cx("group rounded-xl border border-border bg-surface px-4 py-3 text-sm")} data-testid="troubleshooting">
      <summary className="flex cursor-pointer items-center gap-2 font-medium text-muted">
        <LifeBuoy className="size-4" /> Troubleshooting
      </summary>
      <div className="mt-3 space-y-2 text-muted">
        <p>
          <b className="text-fg">The code expired:</b> the watch shows a new code by itself after 5 minutes. Type the new one.
        </p>
        <p>
          <b className="text-fg">A black screen:</b> charge it, then hold the side button until the screen lights up.
        </p>
        <p>
          <b className="text-fg">Start again:</b> hold the top button for 8 seconds and confirm the factory reset — the Wi-Fi screen opens
          again.
        </p>
        <p>
          <b className="text-fg">Still stuck?</b> Email{" "}
          <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent hover:underline">
            {SUPPORT_EMAIL}
          </a>
          .
        </p>
      </div>
    </details>
  );
}

/** What to try first, once the watch is paired. */
export function FirstNoteHint() {
  return (
    <div className="flex items-start gap-3 rounded-xl bg-surface-2 px-4 py-3 text-sm" data-testid="first-note-hint">
      <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full bg-accent text-white">
        <Mic className="size-4" />
      </span>
      <p>
        <b>Try it now:</b> tap the microphone and say “Make a note to buy milk.” Then open Notes on the watch to find it.
      </p>
    </div>
  );
}

function Progress({ ob }: { ob: Onboarding }) {
  const careDone = ob.care === null ? ob.watches > 0 : ob.care.status === "active" || ob.care.status === "not_eligible";
  const steps: [string, boolean][] = [
    ["Order", !!ob.order && ob.order.status !== "payment_pending"],
    ["Password", ob.has_password],
    ["Email", ob.email_verified],
    ["Paired", ob.watches > 0],
    ["olá Care", careDone],
  ];
  return (
    <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1.5 text-xs" aria-label="Setup progress">
      {steps.map(([label, done], i) => (
        <li key={label} className="inline-flex items-center gap-1.5">
          {i > 0 && <ChevronRight className="size-3.5 text-muted/60" aria-hidden />}
          <span className={cx("inline-flex items-center gap-1", done ? "text-ok" : "text-muted")}>
            {done ? <CircleCheck className="size-3.5" aria-hidden /> : <Circle className="size-3.5" aria-hidden />}
            <span>
              {label}
              <span className="sr-only">{done ? " (done)" : " (to do)"}</span>
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function Section({ icon, title, done, muted, children }: { icon: ReactNode; title: string; done: boolean; muted?: boolean; children: ReactNode }) {
  return (
    <Card>
      <h2 className={cx("mb-3 flex items-center gap-2 font-semibold", muted && !done && "text-muted")}>
        <span className={done ? "text-ok" : "text-accent"}>{done ? <CircleCheck className="size-4" /> : icon}</span>
        {title}
      </h2>
      {children}
    </Card>
  );
}

function Notice({ tone, icon, children }: { tone: "warn" | "ok" | "neutral"; icon?: ReactNode; children: ReactNode }) {
  return (
    <div
      role="status"
      className={cx(
        "flex items-start gap-3 rounded-xl px-4 py-3 text-sm",
        tone === "warn" ? "border border-warn/30 bg-warn-bg text-warn" : tone === "ok" ? "bg-ok-bg text-ok" : "bg-surface-2 text-muted",
      )}
    >
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div>{children}</div>
    </div>
  );
}

/** What ola Care costs and when it starts, in every state (displayed status comes from the server). */
export function CareSummary({ care, plan }: { care: CareActivation | null; plan: MyPlan | null }) {
  const price = plan ? pence(plan.prices.care_price_pence) : "";
  const days = care?.trial_days ?? 30;
  if (plan?.status.kind === "trial" && plan.status.trial_end && plan.status.cancel_at_period_end) {
    return (
      <p className="text-sm" data-testid="care-trial-cancelled">
        olá Care is cancelled: your free trial still works until <b>{fmtDayMonth(plan.status.trial_end)}</b> and nothing will be
        charged. To keep it, open Account → olá Care → “Keep olá Care”.
      </p>
    );
  }
  if (plan?.status.kind === "trial" && plan.status.trial_end) {
    return (
      <p className="text-sm" data-testid="care-trial-active">
        Your free trial runs until <b>{fmtDayMonth(plan.status.trial_end)}</b>. Then {price} a month, charged automatically to the card you
        saved at checkout, until you cancel. Cancel any time before that in Account → Manage billing to pay nothing.
      </p>
    );
  }
  if (plan?.status.kind === "active") return <p className="text-sm text-ok">olá Care is active.</p>;
  if (plan?.status.kind === "complimentary") return <p className="text-sm">Your account has complimentary olá Care — nothing to pay.</p>;
  if (!care) return <p className="text-sm text-muted">Your olá Care plan is shown in Account.</p>;
  switch (care.status) {
    case "awaiting_pairing":
      return (
        <p className="text-sm text-muted" data-testid="care-pending-pairing">
          Your {days}-day free trial starts when the watch is paired — not before. Nothing is charged until it ends; then {price} a month
          until you cancel.
        </p>
      );
    case "activating":
      return <p className="text-sm text-muted">Starting your free trial…</p>;
    case "failed":
      return (
        <p className="text-sm text-warn" data-testid="care-setup-pending">
          Subscription setup pending. {careErrorText(care.error)}
        </p>
      );
    case "not_eligible":
      return (
        <p className="text-sm text-muted">
          {care.reason === "trial_used"
            ? "This account already used its free trial. You can subscribe in Account."
            : "No free trial applies to this account. See Account for your plan."}
        </p>
      );
    default:
      return null;
  }
}

function Done({ ob, plan, onPlanChange, onFinish }: { ob: Onboarding; plan: MyPlan | null; onPlanChange: () => void; onFinish: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const retry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.me.activateCare();
      onPlanChange();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <Notice tone="ok" icon={<CircleCheck className="size-4" />}>
        <b>Your watch is paired.</b>
      </Notice>
      <FirstNoteHint />
      <Card title="olá Care" icon={<Sparkles />}>
        <div className="space-y-3">
          <CareSummary care={ob.care} plan={plan} />
          {ob.care?.status === "failed" && (
            <>
              <Button variant="secondary" loading={busy} onClick={retry}>
                Try again
              </Button>
              <ErrorBox error={error} />
            </>
          )}
        </div>
      </Card>
      <Button variant="primary" className="h-11 w-full" onClick={onFinish}>
        Go to my watch
      </Button>
    </div>
  );
}

function NoOrder() {
  const { account, signOut } = useCustomer();
  const navigate = useNavigate();
  const [paid, setPaid] = useState(false);
  const switchAccount = async () => {
    await signOut();
    navigate("/login");
  };
  return (
    <div className="mx-auto max-w-lg">
      <Card>
        <div className="space-y-3 text-center" data-testid="no-order">
          <PackageSearch className="mx-auto size-10 text-muted" />
          <h1 className="text-lg font-semibold">No watch order found</h1>
          <p className="text-sm text-muted">
            Watch setup opens once your order is paid. If you bought a watch with another email address, sign in with that one.
          </p>
          {!paid ? (
            <div className="flex flex-wrap justify-center gap-2">
              <Button variant="primary" onClick={() => setPaid(true)}>
                I’ve already paid
              </Button>
              <a href={`${SITE_URL}/#buy`} className={buttonCls("secondary")}>
                Get an olá watch
              </a>
            </div>
          ) : (
            <div className="space-y-3 rounded-xl bg-surface-2 px-4 py-3 text-left text-sm" data-testid="already-paid">
              <p>
                You’re signed in as <b className="break-all">{account.email}</b>. Your order is linked to the email address on your payment
                confirmation.
              </p>
              <Button variant="primary" className="w-full" onClick={switchAccount}>
                Sign in with that email
              </Button>
              <p className="text-muted">No password yet? Choose “Forgot password?” and we’ll email you a sign-in link.</p>
              <p className="text-muted">
                Still can’t find your order? Email{" "}
                <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent hover:underline">
                  {SUPPORT_EMAIL}
                </a>{" "}
                with your payment confirmation.
              </p>
            </div>
          )}
          <p className="text-xs">
            <Link to="/my" className="text-muted hover:underline">
              Back to my account
            </Link>
          </p>
        </div>
      </Card>
    </div>
  );
}
