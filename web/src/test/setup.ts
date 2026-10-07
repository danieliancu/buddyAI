import { afterEach } from "vitest";
import { cleanup, configure } from "@testing-library/react";
import { webcrypto } from "node:crypto";

// jsdom has no SubtleCrypto: use Node's (the same Web Crypto API as the browser).
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}

// The Bluetooth tests run real SRP-6a (3072-bit BigInt maths) on both sides: allow for slow CI machines.
configure({ asyncUtilTimeout: 10000 });

afterEach(() => cleanup());
