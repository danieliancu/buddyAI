/**
 * ESP-IDF protocomm security scheme 2 session (client side), as in esp_prov security2.py:
 * SRP-6a handshake with the watch's setup password, then AES-256-GCM with the first 32 bytes of the
 * session key and the 12-byte nonce from the watch, whose last 4 bytes count every message
 * (big-endian, sec_patch_ver 1). There is no unauthenticated fallback.
 */
import { parseSec2Response0, parseSec2Response1, sec2Command0, sec2Command1 } from "./proto";
import { SrpClient, SRP_USERNAME } from "./srp6a";

export class WrongSetupPasswordError extends Error {
  constructor() {
    super("The setup password does not match the one shown on the watch.");
    this.name = "WrongSetupPasswordError";
  }
}

export class Sec2Session {
  private srp: SrpClient;
  private key: CryptoKey | null = null;
  private nonce = new Uint8Array(12);

  constructor(
    password: string,
    private readonly patchVersion = 1,
    clientSecret?: bigint, // tests only
  ) {
    this.srp = new SrpClient(SRP_USERNAME, password, clientSecret);
  }

  command0(): Uint8Array {
    return sec2Command0(new TextEncoder().encode(SRP_USERNAME), this.srp.publicKey);
  }

  async response0(buf: Uint8Array): Promise<Uint8Array> {
    const r = parseSec2Response0(buf);
    if (r.status !== 0) throw new Error("the watch refused the setup session");
    const proof = await this.srp.processChallenge(r.salt, r.devicePubkey);
    return sec2Command1(proof);
  }

  async response1(buf: Uint8Array): Promise<void> {
    const r = parseSec2Response1(buf);
    if (r.status !== 0 || !this.srp.verifySession(r.deviceProof)) throw new WrongSetupPasswordError();
    if (r.deviceNonce.length !== 12) throw new Error("invalid session nonce");
    this.nonce = new Uint8Array(r.deviceNonce);
    this.key = await crypto.subtle.importKey("raw", this.srp.sessionKey.slice(0, 32) as BufferSource, "AES-GCM", false, [
      "encrypt",
      "decrypt",
    ]);
  }

  get established(): boolean {
    return this.key !== null;
  }

  private nextNonce(): Uint8Array {
    const current = new Uint8Array(this.nonce);
    if (this.patchVersion >= 1) {
      const view = new DataView(this.nonce.buffer, this.nonce.byteOffset, 12);
      const counter = view.getUint32(8, false);
      if (counter === 0xffffffff) throw new Error("session expired, connect again");
      view.setUint32(8, counter + 1, false);
    }
    return current;
  }

  async encrypt(plain: Uint8Array): Promise<Uint8Array> {
    if (!this.key) throw new Error("no secure session");
    const iv = this.nextNonce();
    return new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: iv as BufferSource }, this.key, plain as BufferSource));
  }

  async decrypt(cipher: Uint8Array): Promise<Uint8Array> {
    if (!this.key) throw new Error("no secure session");
    const iv = this.nextNonce();
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: iv as BufferSource }, this.key, cipher as BufferSource));
  }
}
