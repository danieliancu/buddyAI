/**
 * SRP-6a client for ESP-IDF protocomm security scheme 2 (the watch's Bluetooth setup).
 *
 * Mirrors ESP-IDF's reference client (tools/esp_prov/security/srp6a.py) byte for byte: RFC 5054 3072-bit
 * group, g = 5, SHA-512, username "wifiprov". Like the reference, integers are serialised big-endian
 * without leading zero bytes, except where the reference pads to the length of N (k and u).
 * Checked against vectors generated with esp_prov (src/ble/__fixtures__/srp6a-esp-prov.json).
 */

const N_HEX =
  "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B" +
  "302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE6" +
  "49286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D" +
  "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718" +
  "3995497CEA956AE515D2261898FA051015728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7D" +
  "B3970F85A6E1E4C7ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C" +
  "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF";

export const N = BigInt("0x" + N_HEX);
export const g = 5n;
export const N_LEN = 384;
export const SRP_USERNAME = "wifiprov";

const enc = new TextEncoder();

export function bytesToBig(b: Uint8Array): bigint {
  let n = 0n;
  for (const x of b) n = (n << 8n) | BigInt(x);
  return n;
}

/** Big-endian, no leading zeros (0 -> one zero byte), like esp_prov's long_to_bytes. */
export function bigToBytes(n: bigint): Uint8Array {
  if (n === 0n) return new Uint8Array([0]);
  let hex = n.toString(16);
  if (hex.length % 2) hex = "0" + hex;
  return hexToBytes(hex);
}

export function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function bytesToHex(b: Uint8Array): string {
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

function padTo(b: Uint8Array, len: number): Uint8Array {
  if (b.length >= len) return b;
  const out = new Uint8Array(len);
  out.set(b, len - b.length);
  return out;
}

export function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((s, p) => s + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

async function sha512(...parts: Uint8Array[]): Promise<Uint8Array> {
  return new Uint8Array(await crypto.subtle.digest("SHA-512", concat(...parts) as BufferSource));
}

/** esp_prov's H(): hash of the arguments (integers serialised, optionally padded to `width`), as an integer. */
async function hInt(args: (bigint | Uint8Array)[], width?: number): Promise<bigint> {
  const parts = args.map((a) => {
    const b = typeof a === "bigint" ? bigToBytes(a) : a;
    return width ? padTo(b, width) : b;
  });
  return bytesToBig(await sha512(...parts));
}

export function modPow(base: bigint, exp: bigint, mod: bigint): bigint {
  let result = 1n;
  let b = ((base % mod) + mod) % mod;
  let e = exp;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % mod;
    b = (b * b) % mod;
    e >>= 1n;
  }
  return result;
}

function randomScalar(): bigint {
  const r = crypto.getRandomValues(new Uint8Array(32));
  return bytesToBig(r) | (1n << 255n); // 256 bits, top bit set (as esp_prov)
}

export class SrpClient {
  readonly A: bigint;
  private readonly a: bigint;
  private K: Uint8Array | null = null;
  private hamk: Uint8Array | null = null;
  private ok = false;

  constructor(
    private readonly username: string,
    private readonly password: string,
    a?: bigint, // tests only
  ) {
    let secret = a ?? randomScalar();
    let pub = modPow(g, secret, N);
    // The watch takes A as exactly 384 bytes; pick again in the rare case A is shorter, so the padded
    // wire form and the unpadded form used in the proof are the same bytes.
    while (a === undefined && bigToBytes(pub).length !== N_LEN) {
      secret = randomScalar();
      pub = modPow(g, secret, N);
    }
    this.a = secret;
    this.A = pub;
  }

  get publicKey(): Uint8Array {
    return padTo(bigToBytes(this.A), N_LEN); // the watch expects exactly 384 bytes
  }

  /** Returns the client proof M, or throws if the watch's values fail the SRP-6a safety checks. */
  async processChallenge(saltBytes: Uint8Array, bBytes: Uint8Array): Promise<Uint8Array> {
    const s = bytesToBig(saltBytes);
    const B = bytesToBig(bBytes);
    if (B % N === 0n) throw new Error("invalid watch key");
    const k = await hInt([N, g], N_LEN);
    const u = await hInt([this.A, B], N_LEN);
    if (u === 0n) throw new Error("invalid watch key");
    const inner = await hInt([enc.encode(`${this.username}:${this.password}`)]);
    const x = await hInt([s, inner]);
    const v = modPow(g, x, N);
    const S = modPow(B - k * v, this.a + u * x, N);
    this.K = await sha512(bigToBytes(S));
    const hN = await sha512(bigToBytes(N));
    const hg = await sha512(padTo(bigToBytes(g), bigToBytes(N).length));
    const hNg = hN.map((b, i) => b ^ hg[i]);
    const M = await sha512(hNg, await sha512(enc.encode(this.username)), bigToBytes(s), bigToBytes(this.A), bigToBytes(B), this.K);
    this.hamk = await sha512(bigToBytes(this.A), M, this.K);
    return M;
  }

  /** The watch's proof: true only if the watch knows the same password (mutual authentication). */
  verifySession(deviceProof: Uint8Array): boolean {
    this.ok = !!this.hamk && this.hamk.length === deviceProof.length && this.hamk.every((b, i) => b === deviceProof[i]);
    return this.ok;
  }

  get sessionKey(): Uint8Array {
    if (!this.ok || !this.K) throw new Error("session not authenticated");
    return this.K;
  }
}
