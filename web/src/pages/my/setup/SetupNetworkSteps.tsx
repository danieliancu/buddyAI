import { type ReactNode } from "react";
import { ChevronDown, LifeBuoy } from "lucide-react";
import { cx } from "../../../components/ui";

/** Joining the watch's own "ola-XXXX" setup network: the iPhone way, and the Android recovery path. */
export function SetupNetworkSteps({ platform }: { platform: "android" | "iphone" }) {
  return (
    <ol className="space-y-3" data-testid="softap-steps">
      <NumberedStep n={1} title="Open Wi-Fi setup on the watch">
        A new watch shows it by itself. Otherwise open <b className="text-fg">Settings</b> on the watch and tap{" "}
        <b className="text-fg">Wi-Fi setup</b>. The watch shows a network name like <b className="font-mono text-fg">ola-1A2B</b>, a
        password and a QR code.
      </NumberedStep>
      <NumberedStep n={2} title={`Join ola-XXXX on your ${platform === "iphone" ? "iPhone" : "phone"}`}>
        {platform === "iphone" ? (
          <>
            Open the <b className="text-fg">Camera</b> and point it at the QR code on the watch, then tap <b className="text-fg">Join</b>.
            Or open <b className="text-fg">Settings → Wi-Fi</b>, choose <b className="font-mono text-fg">ola-XXXX</b> and type the
            password shown on the watch.
          </>
        ) : (
          <>
            Open <b className="text-fg">Settings → Wi-Fi</b> (or scan the QR code with the camera), choose{" "}
            <b className="font-mono text-fg">ola-XXXX</b> and type the password shown on the watch.
          </>
        )}
      </NumberedStep>
      <NumberedStep n={3} title="Choose your home Wi-Fi">
        A setup page opens by itself (if not, open a browser and go to <span className="font-mono text-fg">192.168.4.1</span>). Pick
        your home network, enter its password and tap <b className="text-fg">Save</b>. The watch uses <b className="text-fg">2.4 GHz</b>{" "}
        Wi-Fi: choose the 2.4 GHz network if your router has separate ones.
      </NumberedStep>
      <NumberedStep n={4} title="Come back here">
        The watch restarts and joins your Wi-Fi. Your {platform === "iphone" ? "iPhone" : "phone"} goes back to your usual Wi-Fi; return to
        this page to pair the watch.
      </NumberedStep>
    </ol>
  );
}

export function NumberedStep({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <li className="flex gap-3 rounded-xl border border-border bg-surface p-4">
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-accent-bg text-sm font-semibold text-accent">{n}</span>
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        <p className="mt-0.5 text-sm text-muted">{children}</p>
      </div>
    </li>
  );
}

/** Android recovery: hidden unless Bluetooth setup can't be used, failed, or the customer asks for help. */
export function RecoveryHelp({ open, onToggle, reason }: { open: boolean; onToggle: () => void; reason?: ReactNode }) {
  return (
    <div className="rounded-xl border border-border">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left text-sm font-medium"
      >
        <span className="inline-flex items-center gap-2">
          <LifeBuoy className="size-4 text-muted" /> {open ? "Set up Wi-Fi without Bluetooth" : "Need help?"}
        </span>
        <ChevronDown className={cx("size-4 text-muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-4 py-4" data-testid="recovery">
          {reason && <p className="text-sm text-muted">{reason}</p>}
          <SetupNetworkSteps platform="android" />
        </div>
      )}
    </div>
  );
}
