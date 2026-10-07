import { describe, expect, it } from "vitest";
import vectors from "./__fixtures__/srp6a-esp-prov.json";
import { FakeWatch } from "./fakeWatch";
import { decode, encode, num, parseWifiStatus, wifiSetConfig } from "./proto";
import { Provisioner, SetupPausedError } from "./provisioner";
import { Sec2Session, WrongSetupPasswordError } from "./sec2";
import { bigToBytes, bytesToHex, hexToBytes, SrpClient } from "./srp6a";
import { characteristicUuid, OLA_SERVICE } from "./transport";
import { checkWifiCredentials, isSetupPassword, normalizeSetupPassword } from "./validate";

describe("SRP-6a matches ESP-IDF's esp_prov client", () => {
  it("public key, proof, session key and watch proof", async () => {
    const c = new SrpClient(vectors.username, vectors.password, BigInt("0x" + vectors.a));
    expect(bytesToHex(bigToBytes(c.A))).toBe(vectors.A);
    const M = await c.processChallenge(hexToBytes(vectors.salt), hexToBytes(vectors.B));
    expect(bytesToHex(M)).toBe(vectors.M);
    expect(c.verifySession(hexToBytes(vectors.H_AMK))).toBe(true);
    expect(bytesToHex(c.sessionKey)).toBe(vectors.K);
  });

  it("a wrong password gives another proof and no session", async () => {
    const c = new SrpClient(vectors.username, "WRONGPWD", BigInt("0x" + vectors.a));
    const M = await c.processChallenge(hexToBytes(vectors.salt), hexToBytes(vectors.B));
    expect(bytesToHex(M)).toBe(vectors.M_wrong_password);
    expect(c.verifySession(hexToBytes(vectors.H_AMK))).toBe(false);
    expect(() => c.sessionKey).toThrow();
  });

  it("refuses a watch key that is a multiple of N", async () => {
    const c = new SrpClient(vectors.username, vectors.password, BigInt("0x" + vectors.a));
    await expect(c.processChallenge(hexToBytes(vectors.salt), new Uint8Array(384))).rejects.toThrow();
  });
});

describe("security 2 session", () => {
  it("AES-GCM with the counter nonce matches esp_prov (Python cryptography)", async () => {
    const s = new Sec2Session(vectors.password, 1, BigInt("0x" + vectors.a));
    // Drive the handshake with the vector's watch values.
    await s.response0(
      encode([[2, 2], [12, encode([[1, 1], [21, encode([[2, hexToBytes(vectors.B)], [3, hexToBytes(vectors.salt)]])]])]]),
    );
    await s.response1(
      encode([[2, 2], [12, encode([[1, 3], [23, encode([[2, hexToBytes(vectors.H_AMK)], [3, hexToBytes(vectors.gcm.device_nonce)]])]])]]),
    );
    for (const m of vectors.gcm.messages) {
      expect(bytesToHex(await s.encrypt(hexToBytes(m.plain)))).toBe(m.cipher);
    }
  });

  it("a watch that cannot prove the password is rejected", async () => {
    const s = new Sec2Session(vectors.password, 1, BigInt("0x" + vectors.a));
    await s.response0(
      encode([[2, 2], [12, encode([[1, 1], [21, encode([[2, hexToBytes(vectors.B)], [3, hexToBytes(vectors.salt)]])]])]]),
    );
    const forged = encode([[2, 2], [12, encode([[1, 3], [23, encode([[2, new Uint8Array(64)], [3, new Uint8Array(12)]])]])]]);
    await expect(s.response1(forged)).rejects.toBeInstanceOf(WrongSetupPasswordError);
    expect(s.established).toBe(false);
  });

  it("other security schemes are refused (no downgrade)", async () => {
    const s = new Sec2Session(vectors.password);
    const sec1 = encode([[2, 1], [11, encode([[1, 1]])]]);
    await expect(s.response0(sec1)).rejects.toThrow(/security scheme 2/);
  });
});

describe("protobuf messages", () => {
  it("set config carries SSID and passphrase as bytes", () => {
    const p = decode(wifiSetConfig("Home Wi-Fi", "correct horse"));
    expect(num(p, 1)).toBe(2);
    const cmd = decode(p.get(12)![0] as Uint8Array);
    expect(new TextDecoder().decode(cmd.get(1)![0] as Uint8Array)).toBe("Home Wi-Fi");
    expect(new TextDecoder().decode(cmd.get(2)![0] as Uint8Array)).toBe("correct horse");
  });

  it("status: connected, connecting, wrong password, not found", () => {
    const wrap = (status: Uint8Array) => encode([[1, 1], [11, status]]);
    expect(parseWifiStatus(wrap(encode([[11, encode([[1, new TextEncoder().encode("10.0.0.5")]])]])))).toEqual({ state: "connected", ip: "10.0.0.5" });
    expect(parseWifiStatus(wrap(encode([[2, 1]])))).toEqual({ state: "connecting" });
    expect(parseWifiStatus(wrap(encode([[2, 3], [10, 0, { keepZero: true }]])))).toEqual({ state: "failed", reason: "wrong_password" });
    expect(parseWifiStatus(wrap(encode([[2, 3], [10, 1]])))).toEqual({ state: "failed", reason: "not_found" });
  });
});

describe("GATT layout", () => {
  it("characteristic UUIDs replace the service's ffff with the endpoint id", () => {
    expect(OLA_SERVICE).toBe("6f6cffff-6177-4f6c-a5e7-3c9d0b1e5a01");
    expect(characteristicUuid(0xff51)).toBe("6f6cff51-6177-4f6c-a5e7-3c9d0b1e5a01");
  });
});

describe("full Bluetooth setup against a simulated watch", () => {
  const networks = [
    { s: "Garden", r: -80, a: 3 },
    { s: "Home", r: -45, a: 3 },
    { s: "Cafe", r: -60, a: 0 },
  ];

  it("lists networks, sends credentials over the encrypted session and reports success", async () => {
    const watch = new FakeWatch({ password: "K7P4M9XQ", networks });
    const p = new Provisioner(watch, { pollMs: 1 });
    await p.establish("K7P4M9XQ");
    const { networks: list } = await p.networks(true);
    expect(list.map((n) => n.ssid)).toEqual(["Home", "Cafe", "Garden"]); // strongest first
    expect(list.find((n) => n.ssid === "Cafe")!.secured).toBe(false);
    const result = await p.joinWifi("Home", "correct horse");
    expect(result).toEqual({ state: "connected", ip: "192.168.1.42" });
    expect(watch.saved).toEqual({ ssid: "Home", pass: "correct horse" });
  });

  it("wrong setup password", async () => {
    const watch = new FakeWatch({ password: "K7P4M9XQ" });
    await expect(new Provisioner(watch).establish("AAAAAAAA")).rejects.toBeInstanceOf(WrongSetupPasswordError);
    expect(watch.failedSessions).toBe(1);
  });

  it("watch pauses setup after repeated wrong passwords", async () => {
    const watch = new FakeWatch({ password: "K7P4M9XQ" });
    const original = watch.send.bind(watch);
    watch.send = async (ep, data) => {
      if (ep === "prov-session" && num(decode(decode(data).get(12)![0] as Uint8Array), 1) === 0) {
        throw new DOMException("GATT operation failed", "NotSupportedError");
      }
      return original(ep, data);
    };
    await expect(new Provisioner(watch).establish("K7P4M9XQ")).rejects.toBeInstanceOf(SetupPausedError);
  });

  it("wrong Wi-Fi password and network not found are reported, nothing saved, retry works", async () => {
    let attempt = 0;
    const watch = new FakeWatch({
      password: "K7P4M9XQ",
      join: (_ssid, pass) => (++attempt === 1 ? "not_found" : pass === "right-password" ? "connected" : "wrong_password"),
    });
    const p = new Provisioner(watch, { pollMs: 1 });
    await p.establish("K7P4M9XQ");
    expect(await p.joinWifi("Home", "whatever1")).toEqual({ state: "failed", reason: "not_found" });
    expect(await p.joinWifi("Home", "bad-password")).toEqual({ state: "failed", reason: "wrong_password" });
    expect(watch.saved).toBeNull();
    expect((await p.joinWifi("Home", "right-password")).state).toBe("connected");
  });

  it("a dropped connection surfaces as a network error", async () => {
    const watch = new FakeWatch({ password: "K7P4M9XQ" });
    const p = new Provisioner(watch, { pollMs: 1 });
    await p.establish("K7P4M9XQ");
    watch.drop();
    await expect(p.networks()).rejects.toMatchObject({ name: "NetworkError" });
  });
});

describe("input checks shared with the watch", () => {
  it("setup password", () => {
    expect(normalizeSetupPassword("k7p4 m9xq")).toBe("K7P4M9XQ");
    expect(isSetupPassword("K7P4M9XQ")).toBe(true);
    expect(isSetupPassword("K7P4M9X0")).toBe(false); // 0 is never used
    expect(isSetupPassword("K7P4")).toBe(false);
  });

  it("Wi-Fi credentials", () => {
    expect(checkWifiCredentials("Home", "correct horse")).toBeNull();
    expect(checkWifiCredentials("Cafe", "")).toBeNull();
    expect(checkWifiCredentials("", "password1")).toBe("ssid_empty");
    expect(checkWifiCredentials("x".repeat(33), "password1")).toBe("ssid_too_long");
    expect(checkWifiCredentials("Home", "short")).toBe("pass_too_short");
    expect(checkWifiCredentials("Home", "a".repeat(64))).toBeNull(); // 64 hex digits
    expect(checkWifiCredentials("Home", "z".repeat(64))).toBe("pass_too_long");
    expect(checkWifiCredentials("Home", "pässwörd1")).toBe("pass_bad_char");
  });
});
