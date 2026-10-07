/**
 * Web Bluetooth transport to the watch's setup service (Chrome / Edge on Android, HTTPS only).
 * One GATT characteristic per protocomm endpoint: write the request, then read the answer.
 * Service and characteristic UUIDs: firmware/components/ble_prov/ble_prov.c.
 */

export const OLA_SERVICE = "6f6cffff-6177-4f6c-a5e7-3c9d0b1e5a01";
export const ENDPOINTS = {
  "prov-session": 0xff51,
  "prov-config": 0xff52,
  "proto-ver": 0xff53,
  "ola-scan": 0xff54,
} as const;
export type Endpoint = keyof typeof ENDPOINTS;

export function characteristicUuid(id: number): string {
  return OLA_SERVICE.slice(0, 4) + id.toString(16).padStart(4, "0") + OLA_SERVICE.slice(8);
}

/** The few Web Bluetooth types used here (not in TypeScript's DOM library). */
export interface BleCharacteristic {
  writeValueWithResponse(value: BufferSource): Promise<void>;
  readValue(): Promise<DataView>;
}
interface BleService {
  getCharacteristic(uuid: string): Promise<BleCharacteristic>;
}
interface BleServer {
  connected: boolean;
  connect(): Promise<BleServer>;
  disconnect(): void;
  getPrimaryService(uuid: string): Promise<BleService>;
}
export interface BleDevice extends EventTarget {
  name?: string;
  gatt?: BleServer;
}
export interface WebBluetooth {
  getAvailability?(): Promise<boolean>;
  requestDevice(options: { filters: object[]; optionalServices?: string[] }): Promise<BleDevice>;
}

/** Anything that can carry endpoint messages (the real GATT link, or a fake watch in tests). */
export interface Transport {
  send(endpoint: Endpoint, data: Uint8Array): Promise<Uint8Array>;
  disconnect(): void;
  onDisconnect(cb: () => void): void;
  readonly name: string;
  readonly connected: boolean;
}

export function webBluetooth(): WebBluetooth | null {
  const nav = globalThis.navigator as Navigator & { bluetooth?: WebBluetooth };
  return nav?.bluetooth ?? null;
}

export type BleSupport = "supported" | "insecure" | "unsupported" | "ios";

export function bleSupport(): BleSupport {
  const ua = globalThis.navigator?.userAgent ?? "";
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios"; // no Web Bluetooth on iOS browsers
  if (!globalThis.isSecureContext) return "insecure";
  return webBluetooth() ? "supported" : "unsupported";
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new DOMException(`${what} timed out`, "TimeoutError")), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

export class GattTransport implements Transport {
  private chars = new Map<Endpoint, BleCharacteristic>();
  private queue: Promise<unknown> = Promise.resolve();

  private constructor(private readonly device: BleDevice) {}

  get name(): string {
    return this.device.name ?? "ola";
  }

  get connected(): boolean {
    return !!this.device.gatt?.connected;
  }

  /** Shows the browser's device chooser (needs a user tap), then connects. */
  static async choose(bt: WebBluetooth, connectTimeoutMs = 15000): Promise<GattTransport> {
    const device = await bt.requestDevice({
      filters: [{ services: [OLA_SERVICE] }, { namePrefix: "ola-" }],
      optionalServices: [OLA_SERVICE],
    });
    const t = new GattTransport(device);
    await withTimeout(t.open(), connectTimeoutMs, "Connecting to the watch");
    return t;
  }

  private async open(): Promise<void> {
    if (!this.device.gatt) throw new DOMException("no GATT server", "NetworkError");
    const server = await this.device.gatt.connect();
    const service = await server.getPrimaryService(OLA_SERVICE);
    for (const [name, id] of Object.entries(ENDPOINTS) as [Endpoint, number][]) {
      this.chars.set(name, await service.getCharacteristic(characteristicUuid(id)));
    }
  }

  send(endpoint: Endpoint, data: Uint8Array): Promise<Uint8Array> {
    // One request at a time: protocomm answers the last write on a characteristic.
    const run = async () => {
      const ch = this.chars.get(endpoint);
      if (!ch) throw new DOMException(`endpoint ${endpoint} missing`, "NotFoundError");
      await withTimeout(ch.writeValueWithResponse(data as BufferSource), 15000, "Writing to the watch");
      const v = await withTimeout(ch.readValue(), 15000, "Reading from the watch");
      return new Uint8Array(v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength));
    };
    const next = this.queue.then(run, run);
    this.queue = next.catch(() => undefined);
    return next;
  }

  onDisconnect(cb: () => void): void {
    this.device.addEventListener("gattserverdisconnected", cb);
  }

  disconnect(): void {
    try {
      if (this.device.gatt?.connected) this.device.gatt.disconnect();
    } catch {
      /* already gone */
    }
  }
}
