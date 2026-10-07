/**
 * Just enough protobuf for ESP-IDF provisioning: session.proto + sec2.proto (security 2 handshake) and
 * wifi_config.proto (set credentials, apply, read status). proto3 rules: zero values are left out
 * except inside a oneof, so a missing number reads as 0.
 */

export type ProtoValue = number | Uint8Array;
export type ProtoFields = Map<number, ProtoValue[]>;

function varint(n: number): number[] {
  const out: number[] = [];
  let v = n >>> 0;
  while (v > 0x7f) {
    out.push((v & 0x7f) | 0x80);
    v >>>= 7;
  }
  out.push(v);
  return out;
}

/** Fields in order: [number, value]; a number is a varint, bytes are length-delimited (nested messages
 * are encoded bytes). `undefined` and 0 varints are skipped (proto3), unless `keepZero`. */
export function encode(fields: [number, ProtoValue | undefined, { keepZero?: boolean }?][]): Uint8Array {
  const out: number[] = [];
  for (const [num, value, opts] of fields) {
    if (value === undefined) continue;
    if (typeof value === "number") {
      if (value === 0 && !opts?.keepZero) continue;
      out.push(...varint(num << 3), ...varint(value));
    } else {
      out.push(...varint((num << 3) | 2), ...varint(value.length), ...value);
    }
  }
  return new Uint8Array(out);
}

export function decode(buf: Uint8Array): ProtoFields {
  const fields: ProtoFields = new Map();
  let i = 0;
  const readVarint = (): number => {
    let shift = 0;
    let result = 0;
    for (;;) {
      if (i >= buf.length) throw new Error("truncated message");
      const b = buf[i++];
      result += (b & 0x7f) * 2 ** shift;
      if (!(b & 0x80)) return result;
      shift += 7;
      if (shift > 63) throw new Error("bad varint");
    }
  };
  while (i < buf.length) {
    const key = readVarint();
    const num = Math.floor(key / 8);
    const wire = key & 7;
    let value: ProtoValue;
    if (wire === 0) value = readVarint();
    else if (wire === 2) {
      const len = readVarint();
      if (i + len > buf.length) throw new Error("truncated message");
      value = buf.slice(i, i + len);
      i += len;
    } else if (wire === 5) {
      value = buf.slice(i, i + 4);
      i += 4;
    } else if (wire === 1) {
      value = buf.slice(i, i + 8);
      i += 8;
    } else throw new Error(`unsupported wire type ${wire}`);
    const list = fields.get(num) ?? [];
    list.push(value);
    fields.set(num, list);
  }
  return fields;
}

export const num = (f: ProtoFields, n: number): number => {
  const v = f.get(n)?.[0];
  return typeof v === "number" ? v : 0;
};
export const bytes = (f: ProtoFields, n: number): Uint8Array | undefined => {
  const v = f.get(n)?.[0];
  return v instanceof Uint8Array ? v : undefined;
};
export const has = (f: ProtoFields, n: number): boolean => f.has(n);

// --- session.proto / sec2.proto ---------------------------------------------------------------------

const SEC_SCHEME_2 = 2;
export const Sec2Msg = { Command0: 0, Response0: 1, Command1: 2, Response1: 3 } as const;

function sessionData(sec2: Uint8Array): Uint8Array {
  return encode([
    [2, SEC_SCHEME_2],
    [12, sec2],
  ]);
}

export function sec2Command0(username: Uint8Array, clientPubkey: Uint8Array): Uint8Array {
  const sc0 = encode([
    [1, username],
    [2, clientPubkey],
  ]);
  return sessionData(encode([[1, Sec2Msg.Command0], [20, sc0]]));
}

export function sec2Command1(clientProof: Uint8Array): Uint8Array {
  return sessionData(encode([[1, Sec2Msg.Command1], [22, encode([[1, clientProof]])]]));
}

function sec2Payload(buf: Uint8Array): ProtoFields {
  const session = decode(buf);
  if (num(session, 2) !== SEC_SCHEME_2) throw new Error("the watch does not use security scheme 2");
  const sec2 = bytes(session, 12);
  if (!sec2) throw new Error("empty session response");
  return decode(sec2);
}

export function parseSec2Response0(buf: Uint8Array): { status: number; devicePubkey: Uint8Array; salt: Uint8Array } {
  const p = sec2Payload(buf);
  if (num(p, 1) !== Sec2Msg.Response0) throw new Error("unexpected session message");
  const sr0 = decode(bytes(p, 21) ?? new Uint8Array());
  const devicePubkey = bytes(sr0, 2);
  const salt = bytes(sr0, 3);
  if (!devicePubkey || !salt) throw new Error("incomplete session response");
  return { status: num(sr0, 1), devicePubkey, salt };
}

export function parseSec2Response1(buf: Uint8Array): { status: number; deviceProof: Uint8Array; deviceNonce: Uint8Array } {
  const p = sec2Payload(buf);
  if (num(p, 1) !== Sec2Msg.Response1) throw new Error("unexpected session message");
  const sr1 = decode(bytes(p, 23) ?? new Uint8Array());
  const deviceProof = bytes(sr1, 2);
  const deviceNonce = bytes(sr1, 3);
  if (!deviceProof || !deviceNonce) throw new Error("incomplete session response");
  return { status: num(sr1, 1), deviceProof, deviceNonce };
}

// --- wifi_config.proto ------------------------------------------------------------------------------

export const WifiMsg = {
  CmdGetStatus: 0,
  RespGetStatus: 1,
  CmdSetConfig: 2,
  RespSetConfig: 3,
  CmdApplyConfig: 4,
  RespApplyConfig: 5,
} as const;

const EMPTY = new Uint8Array();
const utf8 = new TextEncoder();

export function wifiGetStatus(): Uint8Array {
  return encode([[1, WifiMsg.CmdGetStatus], [10, EMPTY]]);
}

export function wifiSetConfig(ssid: string, passphrase: string): Uint8Array {
  const cmd = encode([
    [1, utf8.encode(ssid)],
    [2, passphrase ? utf8.encode(passphrase) : undefined],
  ]);
  return encode([[1, WifiMsg.CmdSetConfig], [12, cmd]]);
}

export function wifiApplyConfig(): Uint8Array {
  return encode([[1, WifiMsg.CmdApplyConfig], [14, EMPTY]]);
}

export type WifiStatus =
  | { state: "connected"; ip: string }
  | { state: "connecting" }
  | { state: "idle" }
  | { state: "failed"; reason: "wrong_password" | "not_found" };

/** RespGetStatus: sta_state 0 Connected, 1 Connecting, 2 Disconnected, 3 ConnectionFailed + fail_reason
 * (0 AuthError, 1 NetworkNotFound). */
export function parseWifiStatus(buf: Uint8Array): WifiStatus {
  const p = decode(buf);
  if (num(p, 1) !== WifiMsg.RespGetStatus) throw new Error("unexpected Wi-Fi status message");
  const r = decode(bytes(p, 11) ?? new Uint8Array());
  if (num(r, 1) !== 0) throw new Error("the watch could not read its Wi-Fi status");
  const sta = num(r, 2);
  if (sta === 0) {
    const connected = decode(bytes(r, 11) ?? new Uint8Array());
    return { state: "connected", ip: new TextDecoder().decode(bytes(connected, 1) ?? new Uint8Array()) };
  }
  if (sta === 1) return { state: "connecting" };
  if (sta === 3 || has(r, 10)) return { state: "failed", reason: num(r, 10) === 1 ? "not_found" : "wrong_password" };
  return { state: "idle" };
}

/** RespSetConfig / RespApplyConfig: the status code (0 = success, 4 = invalid argument). */
export function parseWifiAck(buf: Uint8Array, expected: typeof WifiMsg.RespSetConfig | typeof WifiMsg.RespApplyConfig): number {
  const p = decode(buf);
  if (num(p, 1) !== expected) throw new Error("unexpected Wi-Fi config message");
  const field = expected === WifiMsg.RespSetConfig ? 13 : 15;
  return num(decode(bytes(p, field) ?? new Uint8Array()), 1);
}
