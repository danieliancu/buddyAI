import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bluetooth, CircleCheck, KeyRound, Loader2, Lock, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { Provisioner, SetupPausedError, type NearbyNetwork } from "../../../ble/provisioner";
import { WrongSetupPasswordError } from "../../../ble/sec2";
import { bleSupport, GattTransport, webBluetooth, type BleSupport } from "../../../ble/transport";
import { checkWifiCredentials, isSetupPassword, normalizeSetupPassword, WIFI_PROBLEM_TEXT } from "../../../ble/validate";
import { Button, Field, Input, Select, cx } from "../../../components/ui";
import { RecoveryHelp } from "./SetupNetworkSteps";

export type ConnectWatch = () => Promise<Provisioner>;

type Phase =
  | { kind: "intro" }
  | { kind: "choosing" }
  | { kind: "password"; prov: Provisioner }
  | { kind: "networks"; prov: Provisioner }
  | { kind: "joining"; prov: Provisioner; ssid: string }
  | { kind: "joined"; ssid: string }
  | { kind: "problem"; problem: Problem; prov?: Provisioner };

type Problem = "cancelled" | "denied" | "timeout" | "disconnected" | "unavailable" | "wrong_wifi_password" | "wifi_not_found" | "error";

const PROBLEM_TEXT: Record<Problem, { title: string; body: string }> = {
  cancelled: {
    title: "No watch selected",
    body: "Make sure the watch shows the Wi-Fi setup screen and is close to your phone, then tap Connect to watch and choose the ola watch in the list.",
  },
  denied: {
    title: "Bluetooth permission needed",
    body: "Allow Chrome to use Bluetooth (and Nearby devices / Location if Android asks), then try again. You can change it in Android Settings → Apps → Chrome → Permissions.",
  },
  timeout: {
    title: "The watch didn't answer",
    body: "Bring the phone closer to the watch and check that the watch still shows the Wi-Fi setup screen.",
  },
  disconnected: {
    title: "Connection to the watch lost",
    body: "The watch went out of range or restarted. Keep the phone near the watch and connect again.",
  },
  unavailable: {
    title: "Bluetooth is off",
    body: "Turn on Bluetooth on your phone, then try again.",
  },
  wrong_wifi_password: {
    title: "Wrong Wi-Fi password",
    body: "The watch found your network, but the password didn't work. Check it (it is case-sensitive) and try again. Nothing was changed on the watch.",
  },
  wifi_not_found: {
    title: "The watch couldn't reach that network",
    body: "The watch uses 2.4 GHz Wi-Fi. Choose the 2.4 GHz network of your router, move the watch closer to it, and try again.",
  },
  error: {
    title: "Something went wrong",
    body: "Try again. If it keeps failing, use the setup network below.",
  },
};

function problemOf(e: unknown): Problem {
  if (e instanceof DOMException) {
    if (e.name === "NotFoundError") return "cancelled"; // chooser closed or no device picked
    if (e.name === "NotAllowedError" || e.name === "SecurityError") return "denied";
    if (e.name === "TimeoutError") return "timeout";
    if (e.name === "NetworkError" || e.name === "InvalidStateError") return "disconnected";
  }
  return "error";
}

const defaultConnect: ConnectWatch = async () => {
  const bt = webBluetooth();
  if (!bt) throw new DOMException("Web Bluetooth unavailable", "NotSupportedError");
  if (bt.getAvailability && !(await bt.getAvailability())) throw new DOMException("Bluetooth off", "InvalidAccessError");
  return new Provisioner(await GattTransport.choose(bt));
};

/** Android: Wi-Fi setup over Bluetooth from Chrome. The ola-XXXX setup network stays available as recovery. */
export default function AndroidWifi({
  onDone,
  support = bleSupport(),
  connect = defaultConnect,
}: {
  onDone: () => void;
  support?: BleSupport;
  connect?: ConnectWatch;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: "intro" });
  const [helpOpen, setHelpOpen] = useState(false);
  const provRef = useRef<Provisioner | null>(null);
  const supported = support === "supported";

  useEffect(() => () => provRef.current?.disconnect(), []);

  const start = async () => {
    setPhase({ kind: "choosing" });
    try {
      const prov = await connect();
      provRef.current = prov;
      // The watch restarts after a successful setup: that disconnect is expected.
      prov.onDisconnect(() => setPhase((p) => (p.kind === "joined" ? p : { kind: "problem", problem: "disconnected" })));
      setPhase({ kind: "password", prov });
    } catch (e) {
      const p = e instanceof DOMException && e.name === "InvalidAccessError" ? "unavailable" : problemOf(e);
      setPhase({ kind: "problem", problem: p });
    }
  };

  const showRecovery = !supported || phase.kind === "problem" || helpOpen;

  return (
    <div className="space-y-4">
      {!supported ? (
        <Unsupported support={support} />
      ) : (
        <div className="rounded-xl border border-border bg-surface p-4">
          {phase.kind === "intro" && <Intro onStart={start} />}
          {phase.kind === "choosing" && <Busy text="Choose your ola watch in the list that opens…" />}
          {phase.kind === "password" && (
            <SetupPassword
              prov={phase.prov}
              onOk={() => setPhase({ kind: "networks", prov: phase.prov })}
              onLost={(e) => setPhase({ kind: "problem", problem: problemOf(e) })}
            />
          )}
          {phase.kind === "networks" && (
            <Networks
              prov={phase.prov}
              onJoin={async (ssid, pass) => {
                setPhase({ kind: "joining", prov: phase.prov, ssid });
                try {
                  const r = await phase.prov.joinWifi(ssid, pass);
                  if (r.state === "connected") {
                    setPhase({ kind: "joined", ssid });
                    window.setTimeout(onDone, 1500);
                  } else
                    setPhase({
                      kind: "problem",
                      problem: r.state === "failed" && r.reason === "wrong_password" ? "wrong_wifi_password" : "wifi_not_found",
                      prov: phase.prov,
                    });
                } catch (e) {
                  setPhase({ kind: "problem", problem: problemOf(e) });
                }
              }}
              onLost={(e) => setPhase({ kind: "problem", problem: problemOf(e) })}
            />
          )}
          {phase.kind === "joining" && <Busy text={`The watch is joining “${phase.ssid}”…`} />}
          {phase.kind === "joined" && (
            <div className="flex items-start gap-3 text-ok" data-testid="ble-success">
              <CircleCheck className="mt-0.5 size-5 shrink-0" />
              <div>
                <p className="font-medium">Your watch is on “{phase.ssid}”</p>
                <p className="text-sm text-muted">It restarts now and shows a 6-digit code. Next: pair it.</p>
              </div>
            </div>
          )}
          {phase.kind === "problem" && (
            <ProblemBox
              problem={phase.problem}
              onRetry={() => {
                if (phase.prov && (phase.problem === "wrong_wifi_password" || phase.problem === "wifi_not_found"))
                  setPhase({ kind: "networks", prov: phase.prov });
                else {
                  provRef.current?.disconnect();
                  void start();
                }
              }}
            />
          )}
        </div>
      )}
      <RecoveryHelp
        open={showRecovery}
        onToggle={() => setHelpOpen((v) => !v)}
        reason={
          !supported
            ? "Use the watch's own setup network instead:"
            : phase.kind === "problem"
              ? "If Bluetooth keeps failing, you can set up Wi-Fi with the watch's setup network instead:"
              : undefined
        }
      />
    </div>
  );
}

function Intro({ onStart }: { onStart: () => void }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        Keep the watch next to your phone, on its Wi-Fi setup screen. Your phone sends your home Wi-Fi to the watch over Bluetooth —
        encrypted, and only after you type the setup password shown on the watch.
      </p>
      <p className="flex items-start gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">
        <Wifi className="mt-0.5 size-3.5 shrink-0" /> The watch uses 2.4 GHz Wi-Fi. Most home routers offer it; if yours shows two
        networks, choose the 2.4 GHz one.
      </p>
      <Button variant="primary" className="h-12 w-full text-base" icon={<Bluetooth className="size-5" />} onClick={onStart}>
        Connect to watch
      </Button>
    </div>
  );
}

function Busy({ text }: { text: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted" role="status">
      <Loader2 className="size-4 animate-spin" /> {text}
    </p>
  );
}

function SetupPassword({ prov, onOk, onLost }: { prov: Provisioner; onOk: () => void; onLost: (e: unknown) => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pass = normalizeSetupPassword(value);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!isSetupPassword(pass)) return setError("The setup password has 8 letters and numbers, as shown on the watch.");
    setBusy(true);
    setError("");
    try {
      await prov.establish(pass);
      onOk();
    } catch (err) {
      setBusy(false);
      if (err instanceof WrongSetupPasswordError || err instanceof SetupPausedError) setError(err.message);
      else onLost(err);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <p className="flex items-center gap-2 font-medium">
        <KeyRound className="size-4 text-accent" /> Connected to {prov.watchName}
      </p>
      <Field label="Setup password shown on the watch" htmlFor="setup-pass" hint="8 characters, for example K7P4 M9XQ">
        <Input
          id="setup-pass"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          maxLength={11}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="h-14 text-center font-mono text-2xl tracking-[0.3em] uppercase"
          autoFocus
        />
      </Field>
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <Button type="submit" variant="primary" className="h-11 w-full" loading={busy}>
        Continue
      </Button>
    </form>
  );
}

function Networks({
  prov,
  onJoin,
  onLost,
}: {
  prov: Provisioner;
  onJoin: (ssid: string, pass: string) => void;
  onLost: (e: unknown) => void;
}) {
  const [list, setList] = useState<NearbyNetwork[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [ssid, setSsid] = useState("");
  const [other, setOther] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const load = async (refresh: boolean) => {
    setScanning(true);
    try {
      let r = await prov.networks(refresh);
      for (let i = 0; r.scanning && i < 8; i++) {
        await new Promise((res) => setTimeout(res, 800));
        r = await prov.networks(false);
      }
      setList(r.networks);
      setSsid((cur) => cur || r.networks[0]?.ssid || "");
      if (r.networks.length === 0) setOther(true);
    } catch (e) {
      onLost(e);
    } finally {
      setScanning(false);
    }
  };

  useEffect(() => {
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = list?.find((n) => n.ssid === ssid);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const problem = checkWifiCredentials(ssid.trim(), password);
    if (problem) return setError(WIFI_PROBLEM_TEXT[problem]);
    if (selected?.secured && !password) return setError("Enter the password of this network.");
    setError("");
    onJoin(ssid.trim(), password);
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium">Your home Wi-Fi</p>
        <Button type="button" size="sm" variant="ghost" icon={<RefreshCw className={cx("size-3.5", scanning && "animate-spin")} />} onClick={() => load(true)} disabled={scanning}>
          Refresh
        </Button>
      </div>
      {list === null ? (
        <Busy text="Looking for networks near the watch…" />
      ) : (
        <>
          {!other && list.length > 0 ? (
            <Field label="Network" htmlFor="wifi-ssid">
              <Select id="wifi-ssid" value={ssid} onChange={(e) => setSsid(e.target.value)}>
                {list.map((n) => (
                  <option key={n.ssid} value={n.ssid}>
                    {n.ssid} {n.secured ? "🔒" : ""} {signal(n.rssi)}
                  </option>
                ))}
              </Select>
            </Field>
          ) : (
            <Field label="Network name" htmlFor="wifi-ssid-other" hint="Exactly as it appears on your router or phone.">
              <Input id="wifi-ssid-other" value={ssid} maxLength={32} onChange={(e) => setSsid(e.target.value)} autoComplete="off" />
            </Field>
          )}
          <button
            type="button"
            className="text-xs text-accent hover:underline"
            onClick={() => {
              setOther((v) => !v);
              setSsid("");
            }}
          >
            {other ? "Choose from the list" : "My network isn't listed"}
          </button>
          <Field label="Wi-Fi password" htmlFor="wifi-pass">
            <Input
              id="wifi-pass"
              type="password"
              autoComplete="off"
              value={password}
              maxLength={64}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <p className="flex items-start gap-2 text-xs text-muted">
            <Lock className="mt-0.5 size-3.5 shrink-0" /> Sent encrypted, straight to your watch. ola never stores your Wi-Fi password.
            Only 2.4 GHz networks are shown.
          </p>
          {error && (
            <p className="text-sm text-danger" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" className="h-11 w-full" icon={<Wifi className="size-4" />}>
            Send to watch
          </Button>
        </>
      )}
    </form>
  );
}

function signal(rssi: number): string {
  return rssi >= -60 ? "▂▄▆" : rssi >= -72 ? "▂▄" : "▂";
}

function ProblemBox({ problem, onRetry }: { problem: Problem; onRetry: () => void }) {
  const t = PROBLEM_TEXT[problem];
  return (
    <div className="space-y-3" role="alert" data-testid={`ble-problem-${problem}`}>
      <p className="flex items-center gap-2 font-medium text-warn">
        <WifiOff className="size-4" /> {t.title}
      </p>
      <p className="text-sm text-muted">{t.body}</p>
      <Button variant="secondary" onClick={onRetry} icon={<RefreshCw className="size-4" />}>
        Try again
      </Button>
    </div>
  );
}

function Unsupported({ support }: { support: BleSupport }) {
  const text =
    support === "insecure"
      ? "Bluetooth setup needs the secure ola account page (https). Open app.olacompanion.com in Chrome."
      : support === "ios"
        ? "This looks like an iPhone or iPad: choose iPhone above for the right steps."
        : "This browser can't talk to the watch over Bluetooth. Open your ola account in Google Chrome on Android, or use the watch's setup network below.";
  return (
    <div className="rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn" role="alert" data-testid="ble-unsupported">
      {text}
    </div>
  );
}
