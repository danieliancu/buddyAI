/**
 * A simulated watch for tests: speaks the same protocomm protocol as firmware/components/ble_prov
 * (security 2 server side with SRP-6a, AES-GCM with the counter nonce, wifi_config protobuf, ola-scan).
 * Used to test the Bluetooth setup flow without hardware; not part of the app bundle.
 */
import { bytes, decode, encode, num } from "./proto";
import { bigToBytes, bytesToBig, concat, g, modPow, N, N_LEN } from "./srp6a";
import type { Endpoint, Transport } from "./transport";

async function sha512(...parts: Uint8Array[]): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-512", concat(...parts) as BufferSource));
}
const pad = (b: Uint8Array, len: number) => (b.length >= len ? b : concat(new Uint8Array(len - b.length), b));
const hInt = async (args: (bigint | Uint8Array)[], width?: number) =>
  bytesToBig(await sha512(...args.map((a) => (typeof a === "bigint" ? bigToBytes(a) : a)).map((b) => (width ? pad(b, width) : b))));

export interface FakeWatchOptions {
  password: string;
  networks?: { s: string; r: number; a: number }[];
  /** What happens when the phone applies credentials. */
  join?: (ssid: string, pass: string) => "connected" | "wrong_password" | "not_found";
  statusPollsBeforeResult?: number;
}

class GattError extends DOMException {
  constructor() {
    super("GATT operation failed for unknown reason.", "NotSupportedError");
  }
}

export class FakeWatch implements Transport {
  name = "ola-1A2B";
  connected = true;
  failedSessions = 0;
  saved: { ssid: string; pass: string } | null = null;
  private b = bytesToBig(crypto.getRandomValues(new Uint8Array(32)));
  private salt = crypto.getRandomValues(new Uint8Array(16));
  private A = 0n;
  private B = 0n;
  private K: Uint8Array | null = null;
  private key: CryptoKey | null = null;
  private nonce: Uint8Array = new Uint8Array(12);
  private pending: { ssid: string; pass: string } | null = null;
  private state: "idle" | "connecting" | "connected" | "failed" = "idle";
  private reason = 0;
  private polls = 0;
  private listeners: (() => void)[] = [];

  constructor(private readonly opts: FakeWatchOptions) {}

  onDisconnect(cb: () => void): void {
    this.listeners.push(cb);
  }

  disconnect(): void {
    this.connected = false;
  }

  /** Simulates the watch going out of range. */
  drop(): void {
    this.connected = false;
    this.listeners.forEach((cb) => cb());
  }

  async send(endpoint: Endpoint, data: Uint8Array): Promise<Uint8Array> {
    if (!this.connected) throw new DOMException("GATT Server is disconnected.", "NetworkError");
    if (endpoint === "proto-ver") {
      return new TextEncoder().encode('{"prov":{"ver":"v1.1","sec_ver":2,"sec_patch_ver":1,"cap":[]},"ola":{"ver":1}}');
    }
    if (endpoint === "prov-session") return this.session(data);
    if (!this.key) throw new GattError();
    const plain = await this.decrypt(data);
    const reply = endpoint === "ola-scan" ? this.scan() : this.config(plain);
    return this.encrypt(await reply);
  }

  private async session(data: Uint8Array): Promise<Uint8Array> {
    const sd = decode(data);
    const p = decode(bytes(sd, 12)!);
    const v = await this.verifier();
    if (num(p, 1) === 0) {
      const sc0 = decode(bytes(p, 20)!);
      const pub = bytes(sc0, 2)!;
      if (pub.length !== N_LEN) throw new GattError();
      this.A = bytesToBig(pub);
      const k = await hInt([N, g], N_LEN);
      this.B = (k * v + modPow(g, this.b, N)) % N;
      const sr0 = encode([[2, pad(bigToBytes(this.B), N_LEN)], [3, this.salt]]);
      return encode([[2, 2], [12, encode([[1, 1], [21, sr0]])]]);
    }
    const sc1 = decode(bytes(p, 22)!);
    const clientProof = bytes(sc1, 1)!;
    const u = await hInt([this.A, this.B], N_LEN);
    const S = modPow(this.A * modPow(v, u, N), this.b, N);
    this.K = await sha512(bigToBytes(S));
    const hN = await sha512(bigToBytes(N));
    const hg = await sha512(pad(bigToBytes(g), bigToBytes(N).length));
    const M = await sha512(
      hN.map((x, i) => x ^ hg[i]),
      await sha512(new TextEncoder().encode("wifiprov")),
      bigToBytes(bytesToBig(this.salt)),
      bigToBytes(this.A),
      bigToBytes(this.B),
      this.K,
    );
    if (M.length !== clientProof.length || M.some((x, i) => x !== clientProof[i])) {
      this.failedSessions++;
      throw new GattError(); // the watch fails the write on a wrong password
    }
    const hamk = await sha512(bigToBytes(this.A), M, this.K);
    this.nonce = concat(crypto.getRandomValues(new Uint8Array(8)), new Uint8Array([0, 0, 0, 1]));
    this.key = await crypto.subtle.importKey("raw", this.K.slice(0, 32) as BufferSource, "AES-GCM", false, ["encrypt", "decrypt"]);
    const sr1 = encode([[2, hamk], [3, this.nonce]]);
    return encode([[2, 2], [12, encode([[1, 3], [23, sr1]])]]);
  }

  private async verifier(): Promise<bigint> {
    const inner = await hInt([new TextEncoder().encode(`wifiprov:${this.opts.password}`)]);
    const x = await hInt([bytesToBig(this.salt), inner]);
    return modPow(g, x, N);
  }

  private nextNonce(): Uint8Array {
    const cur = new Uint8Array(this.nonce);
    const view = new DataView(this.nonce.buffer);
    view.setUint32(8, view.getUint32(8) + 1);
    return cur;
  }

  private async decrypt(c: Uint8Array): Promise<Uint8Array> {
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: this.nextNonce() as BufferSource }, this.key!, c as BufferSource));
  }

  private async encrypt(p: Uint8Array): Promise<Uint8Array> {
    return new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: this.nextNonce() as BufferSource }, this.key!, p as BufferSource));
  }

  private async scan(): Promise<Uint8Array> {
    return new TextEncoder().encode(JSON.stringify({ scanning: false, aps: this.opts.networks ?? [] }));
  }

  private async config(plain: Uint8Array): Promise<Uint8Array> {
    const p = decode(plain);
    const msg = num(p, 1);
    if (msg === 2) {
      const c = decode(bytes(p, 12)!);
      const dec = new TextDecoder();
      const ssid = dec.decode(bytes(c, 1) ?? new Uint8Array());
      const pass = dec.decode(bytes(c, 2) ?? new Uint8Array());
      const ok = ssid.length > 0 && (pass.length === 0 || pass.length >= 8);
      if (ok) this.pending = { ssid, pass };
      return encode([[1, 3], [13, encode([[1, ok ? 0 : 4]])]]);
    }
    if (msg === 4) {
      if (!this.pending) return encode([[1, 5], [15, encode([[1, 5]])]]);
      this.state = "connecting";
      this.polls = 0;
      return encode([[1, 5], [15, new Uint8Array()]]);
    }
    // get status
    if (this.state === "connecting" && ++this.polls > (this.opts.statusPollsBeforeResult ?? 1)) {
      const r = (this.opts.join ?? (() => "connected"))(this.pending!.ssid, this.pending!.pass);
      if (r === "connected") {
        this.state = "connected";
        this.saved = this.pending;
      } else {
        this.state = "failed";
        this.reason = r === "not_found" ? 1 : 0;
      }
    }
    let status: Uint8Array;
    if (this.state === "connected") status = encode([[11, encode([[1, new TextEncoder().encode("192.168.1.42")]])]]);
    else if (this.state === "connecting") status = encode([[2, 1]]);
    else if (this.state === "failed") status = encode([[2, 3], [10, this.reason, { keepZero: true }]]);
    else status = encode([[2, 2]]);
    return encode([[1, 1], [11, status]]);
  }
}
