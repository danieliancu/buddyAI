import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { ChevronRight, CircleCheck, Circle, MailWarning, PackageSearch, Sparkles, Watch, Wifi } from "lucide-react";
import { api, type CareActivation, type MyPlan, type Onboarding, type SetupPlatform } from "../../../api";
import { careErrorText, fmtDayMonth, pence } from "../../../components/BillingBits";
import { Button, Card, ErrorBox, Spinner, buttonCls, cx, useAsync } from "../../../components/ui";
import PairCodeForm from "../PairCodeForm";
import { useCustomer } from "../session";
import AndroidWifi, { type ConnectWatch } from "./AndroidWifi";
import { AndroidIcon, AppleIcon } from "./PlatformIcons";
import { SetupNetworkSteps } from "./SetupNetworkSteps";
import type { BleSupport } from "../../../ble/transport";
import { SITE_URL } from "../../../site";


/** Remembered on this device only: whether the customer finished the Wi-Fi step (the server can't know). */
function wifiDoneKey(accountId: number) {
  return `ola.setup.wifiDone.${accountId}`;
}
function readWifiDone(accountId: number): boolean {
  try {
    return localStorage.getItem(wifiDoneKey(accountId)) === "1";
  } catch {
    return false;
  }
}
function writeWifiDone(accountId: number, done: boolean) {
  try {
    if (done) localStorage.setItem(wifiDoneKey(accountId), "1");
    else localStorage.removeItem(wifiDoneKey(accountId));
  } catch {
    /* private mode: the step just shows again */
  }
}

/**
 * /my/setup: from a paid order to a working watch. Which phone → Wi-Fi (Bluetooth on Android, the ola-XXXX
 * setup network on iPhone) → pair → ola Care trial. Progress comes from the server (order, password, email,
 * phone choice, paired watches, trial), so a refresh or a later sign-in continues where the customer left off.
 */
export default function SetupPage({ bleSupport, connectWatch }: { bleSupport?: BleSupport; connectWatch?: ConnectWatch } = {}) {
  const { account } = useCustomer();
  const onboarding = useAsync(api.me.onboarding, []);
  const plan = useAsync(api.me.plan, []);
  const [wifiDone, setWifiDone] = useState(() => readWifiDone(account.id));
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();

  useEffect(() => writeWifiDone(account.id, wifiDone), [account.id, wifiDone]);

  if (onboarding.error) return <ErrorBox error={onboarding.error} onRetry={onboarding.reload} />;
  const ob = onboarding.data;
  if (!ob) return <Spinner />;

  const choose = async (platform: SetupPlatform | null) => {
    setSaving(true);
    try {
      onboarding.setData(await api.me.setPlatform(platform));
      if (platform === null) setWifiDone(false);
    } finally {
      setSaving(false);
    }
  };

  if (!ob.eligible) return <NoOrder />;
  const paymentPending = ob.order?.status === "payment_pending";
  const paired = ob.watches > 0;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Set up your ola watch</h1>
        <p className="mt-1 text-sm text-muted">About five minutes. Keep the watch charged and next to your phone.</p>
      </header>

      <Progress ob={ob} wifiDone={wifiDone || paired} />

      {paymentPending ? (
        <Notice tone="warn">
          Your bank is still confirming the payment. We'll email you as soon as it's confirmed; you can set up the watch after that.
        </Notice>
      ) : paired ? (
        <Done ob={ob} plan={plan.data} onPlanChange={() => (onboarding.reload(), plan.reload())} onFinish={() => navigate("/my")} />
      ) : !ob.platform ? (
        <PlatformChoice onChoose={choose} busy={saving} />
      ) : (
        <>
          <div className="flex items-center justify-between gap-2 rounded-xl bg-surface-2 px-4 py-2.5 text-sm">
            <span className="inline-flex items-center gap-2 font-medium">
              {ob.platform === "android" ? <AndroidIcon className="size-4 text-[#3ddc84]" /> : <AppleIcon className="size-4" />}
              {ob.platform === "android" ? "Android phone" : "iPhone"}
            </span>
            <button type="button" className="text-accent hover:underline" onClick={() => choose(null)} disabled={saving}>
              Change phone
            </button>
          </div>

          <Section icon={<Wifi className="size-4" />} title="1. Connect the watch to your Wi-Fi" done={wifiDone}>
            {wifiDone ? (
              <p className="text-sm text-muted">
                Done.{" "}
                <button type="button" className="text-accent hover:underline" onClick={() => setWifiDone(false)}>
                  Set up Wi-Fi again
                </button>
              </p>
            ) : ob.platform === "android" ? (
              <AndroidWifi onDone={() => setWifiDone(true)} support={bleSupport} connect={connectWatch} />
            ) : (
              <div className="space-y-3">
                <SetupNetworkSteps platform="iphone" />
                <Button variant="primary" className="h-11 w-full" onClick={() => setWifiDone(true)}>
                  The watch is on my Wi-Fi
                </Button>
              </div>
            )}
          </Section>

          <Section icon={<Watch className="size-4" />} title="2. Pair the watch with your account" done={false}>
            {!ob.email_verified ? (
              <Notice tone="warn" icon={<MailWarning className="size-4" />}>
                Confirm your email address first: use the link we sent to <b className="break-all">{account.email}</b>, or “Resend email”
                above.
              </Notice>
            ) : (
              <div className="space-y-3">
                <p className="text-sm text-muted">
                  {wifiDone
                    ? "After joining your Wi-Fi the watch shows a 6-digit code. Type it here — it's valid for 5 minutes."
                    : "Once the watch is on your Wi-Fi it shows a 6-digit code. Type it here — it's valid for 5 minutes."}
                </p>
                <PairCodeForm onPaired={() => (setWifiDone(true), onboarding.reload(), plan.reload())} />
              </div>
            )}
          </Section>

          <Section icon={<Sparkles className="size-4" />} title="3. ola Care free trial" done={false} muted>
            <CareSummary care={ob.care} plan={plan.data} />
          </Section>
        </>
      )}
    </div>
  );
}

function Progress({ ob, wifiDone }: { ob: Onboarding; wifiDone: boolean }) {
  const careDone = ob.care === null ? ob.watches > 0 : ob.care.status === "active" || ob.care.status === "not_eligible";
  const steps: [string, boolean][] = [
    ["Order", !!ob.order && ob.order.status !== "payment_pending"],
    ["Password", ob.has_password],
    ["Email", ob.email_verified],
    ["Phone", !!ob.platform || ob.watches > 0],
    ["Wi-Fi", wifiDone],
    ["Paired", ob.watches > 0],
    ["ola Care", careDone],
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

function PlatformChoice({ onChoose, busy }: { onChoose: (p: SetupPlatform) => void; busy: boolean }) {
  return (
    <section aria-labelledby="which-phone" className="space-y-3">
      <h2 id="which-phone" className="text-lg font-semibold">
        Which phone are you using?
      </h2>
      <div className="grid grid-cols-2 gap-3">
        <PlatformButton label="Android" hint="Samsung, Pixel, Xiaomi…" onClick={() => onChoose("android")} disabled={busy}>
          <AndroidIcon className="size-12 text-[#3ddc84]" />
        </PlatformButton>
        <PlatformButton label="iPhone" hint="Apple" onClick={() => onChoose("iphone")} disabled={busy}>
          <AppleIcon className="size-12" />
        </PlatformButton>
      </div>
      <p className="text-xs text-muted">You can change this later.</p>
    </section>
  );
}

function PlatformButton({
  label,
  hint,
  onClick,
  disabled,
  children,
}: {
  label: string;
  hint: string;
  onClick: () => void;
  disabled: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-2xl border border-border bg-surface p-4 text-center transition hover:border-accent hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-60"
    >
      {children}
      <span>
        <span className="block text-base font-semibold">{label}</span>
        <span className="block text-xs text-muted">{hint}</span>
      </span>
    </button>
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
  const days = care?.trial_days ?? 90;
  if (plan?.status.kind === "trial" && plan.status.trial_end) {
    return (
      <p className="text-sm" data-testid="care-trial-active">
        Your free trial runs until <b>{fmtDayMonth(plan.status.trial_end)}</b>. Then {price} a month, charged automatically to the card you
        saved at checkout, until you cancel. Cancel any time before that in Account → Manage billing to pay nothing.
      </p>
    );
  }
  if (plan?.status.kind === "active") return <p className="text-sm text-ok">ola Care is active.</p>;
  if (plan?.status.kind === "complimentary") return <p className="text-sm">Your account has complimentary ola Care — nothing to pay.</p>;
  if (!care) return <p className="text-sm text-muted">Your ola Care plan is shown in Account.</p>;
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
        <b>Your watch is paired.</b> Tap the watch and start talking.
      </Notice>
      <Card title="ola Care">
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
  return (
    <div className="mx-auto max-w-lg">
      <Card>
        <div className="space-y-3 text-center" data-testid="no-order">
          <PackageSearch className="mx-auto size-10 text-muted" />
          <h1 className="text-lg font-semibold">No watch order found</h1>
          <p className="text-sm text-muted">
            Watch setup opens once your order is paid. If you bought a watch with another email address, sign in with that one.
          </p>
          <a href={`${SITE_URL}/#buy`} className={buttonCls("primary")}>
            Get an ola watch
          </a>
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
