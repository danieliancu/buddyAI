/**
 * Bluetooth Wi-Fi setup of an ola watch: secure session with the setup password shown on the watch,
 * nearby networks, credentials, then the watch's own connection result. Protocol details:
 * protocol/BLE_PROVISIONING.md.
 */
import {
  parseWifiAck,
  parseWifiStatus,
  WifiMsg,
  wifiApplyConfig,
  wifiGetStatus,
  wifiSetConfig,
  type WifiStatus,
} from "./proto";
import { Sec2Session, WrongSetupPasswordError } from "./sec2";
import type { Transport } from "./transport";

export interface NearbyNetwork {
  ssid: string;
  rssi: number;
  secured: boolean;
}

export class SetupPausedError extends Error {
  constructor() {
    super("Too many wrong setup passwords. Wait 30 seconds, then try again.");
    this.name = "SetupPausedError";
  }
}

const utf8 = new TextEncoder();
const text = new TextDecoder();

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class Provisioner {
  private session: Sec2Session | null = null;

  constructor(
    private readonly transport: Transport,
    private readonly opts: { clientSecret?: bigint; pollMs?: number; timeoutMs?: number } = {},
  ) {}

  get watchName(): string {
    return this.transport.name;
  }

  /** proto-ver: the scheme the watch uses (must be security 2). */
  async version(): Promise<{ secVer: number; patchVer: number }> {
    const raw = await this.transport.send("proto-ver", utf8.encode("ESP"));
    const v = JSON.parse(text.decode(raw)) as { prov?: { sec_ver?: number; sec_patch_ver?: number } };
    return { secVer: v.prov?.sec_ver ?? 0, patchVer: v.prov?.sec_patch_ver ?? 0 };
  }

  async establish(setupPassword: string): Promise<void> {
    const { secVer, patchVer } = await this.version();
    if (secVer !== 2) throw new Error("This watch needs a firmware update before Bluetooth setup.");
    const s = new Sec2Session(setupPassword, patchVer, this.opts.clientSecret);
    let r0: Uint8Array;
    try {
      r0 = await this.transport.send("prov-session", s.command0());
    } catch (e) {
      // After 3 wrong passwords the watch refuses new sessions for 30 seconds.
      if (this.transport.connected) throw new SetupPausedError();
      throw e;
    }
    let r1: Uint8Array;
    try {
      r1 = await this.transport.send("prov-session", await s.response0(r0));
    } catch (e) {
      // The watch rejects a wrong proof by failing the request (the link stays up).
      if (this.transport.connected) throw new WrongSetupPasswordError();
      throw e;
    }
    await s.response1(r1);
    this.session = s;
  }

  private async call(endpoint: "prov-config" | "ola-scan", payload: Uint8Array): Promise<Uint8Array> {
    if (!this.session) throw new Error("no secure session");
    const reply = await this.transport.send(endpoint, await this.session.encrypt(payload));
    return this.session.decrypt(reply);
  }

  async networks(refresh = false): Promise<{ scanning: boolean; networks: NearbyNetwork[] }> {
    const raw = await this.call("ola-scan", utf8.encode(JSON.stringify(refresh ? { refresh: true } : {})));
    const data = JSON.parse(text.decode(raw)) as { scanning?: boolean; aps?: { s: string; r: number; a: number }[] };
    const networks = (data.aps ?? [])
      .filter((ap) => ap.s)
      .map((ap) => ({ ssid: ap.s, rssi: ap.r, secured: ap.a !== 0 }))
      .sort((x, y) => y.rssi - x.rssi);
    return { scanning: !!data.scanning, networks };
  }

  async status(): Promise<WifiStatus> {
    return parseWifiStatus(await this.call("prov-config", wifiGetStatus()));
  }

  /** Sends the home Wi-Fi to the watch and waits for its own connection result. */
  async joinWifi(ssid: string, password: string, onProgress?: (s: WifiStatus) => void): Promise<WifiStatus> {
    const set = parseWifiAck(await this.call("prov-config", wifiSetConfig(ssid, password)), WifiMsg.RespSetConfig);
    if (set !== 0) throw new Error("The watch did not accept that network name or password.");
    const apply = parseWifiAck(await this.call("prov-config", wifiApplyConfig()), WifiMsg.RespApplyConfig);
    if (apply !== 0) throw new Error("The watch is busy. Try again in a moment.");
    const deadline = Date.now() + (this.opts.timeoutMs ?? 60000);
    for (;;) {
      await sleep(this.opts.pollMs ?? 1000);
      const s = await this.status();
      onProgress?.(s);
      if (s.state === "connected" || s.state === "failed") return s;
      if (Date.now() > deadline) return { state: "failed", reason: "not_found" };
    }
  }

  onDisconnect(cb: () => void): void {
    this.transport.onDisconnect(cb);
  }

  disconnect(): void {
    this.transport.disconnect();
  }
}
