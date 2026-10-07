import { type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowLeft, MailWarning, Power, Smartphone, Watch, Sparkles } from "lucide-react";
import PairCodeForm from "./PairCodeForm";
import { useCustomer } from "./session";

/** Pair another watch (or re-pair one). New owners get the guided setup at /my/setup. */
export default function AddWatchPage() {
  const { account } = useCustomer();
  const navigate = useNavigate();
  const verified = account.email_verified;

  return (
    <div className="mx-auto max-w-lg">
      <Link to="/my" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> My watches
      </Link>
      <h1 className="mb-1 text-xl font-semibold tracking-tight sm:text-2xl">Add a watch</h1>
      <p className="mb-5 text-sm text-muted">
        Three quick steps. Keep your phone close to the watch.{" "}
        <Link to="/my/setup" className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
          <Sparkles className="size-3.5" /> Guided setup for Android and iPhone
        </Link>
      </p>

      <ol className="mb-5 space-y-3">
        <Step n={1} icon={<Power className="size-4" />} title="Turn on the watch">
          Hold the side button until the screen lights up.
        </Step>
        <Step n={2} icon={<Smartphone className="size-4" />} title="Connect it to your Wi-Fi">
          On Android, use <b className="text-fg">Connect to watch</b> in the guided setup. On iPhone, join the network{" "}
          <b className="font-mono text-fg">ola-XXXX</b> with the password shown on the watch (or scan its QR code with the
          Camera); a page opens: pick your home Wi-Fi (2.4 GHz) and enter its password.
        </Step>
        <Step n={3} icon={<Watch className="size-4" />} title="Enter the code">
          The watch shows a 6-digit code. Type it below — it's valid for 5 minutes.
        </Step>
      </ol>

      {!verified ? (
        <div className="flex items-start gap-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn">
          <MailWarning className="mt-0.5 size-4 shrink-0" />
          <p>
            Before adding a watch, please confirm your email address using the link we sent to <b className="break-all">{account.email}</b>.
            You can resend it from the banner above.
          </p>
        </div>
      ) : (
        <PairCodeForm onPaired={(id) => navigate(`/my/watch/${encodeURIComponent(id)}`, { replace: true })} />
      )}
    </div>
  );
}

function Step({ n, icon, title, children }: { n: number; icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3 rounded-xl border border-border bg-surface p-4">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent-bg text-sm font-semibold text-accent">{n}</span>
      <div className="min-w-0">
        <p className="flex items-center gap-2 font-medium">
          {title} <span className="text-muted">{icon}</span>
        </p>
        <p className="mt-0.5 text-sm text-muted">{children}</p>
      </div>
    </li>
  );
}
