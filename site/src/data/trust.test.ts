import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { STATUS, flow, items, specs } from "./trust";

/* /trust/ must never overclaim: every statement has a known status, and no wording promises absolute
 * security, end-to-end encryption or certification that we can't back up. */
const page = readFileSync(resolve(__dirname, "../pages/trust.astro"), "utf8");
const texts = [
  ...items.flatMap((i) => [i.title, i.text]),
  ...specs.flatMap((s) => [s.label, s.value, s.note ?? ""]),
  ...flow.flatMap((f) => [f.title, f.text]),
  page,
].join("\n");

describe("trust & compliance page", () => {
  it("gives every statement a known status", () => {
    for (const x of [...items, ...specs]) expect(Object.keys(STATUS)).toContain(x.status);
  });

  it("makes no absolute or certification claims", () => {
    const banned = [
      /nobody can/i,
      /no one can/i,
      /only you can (see|read|access)/i,
      /\bimpossible\b/i,
      /military[- ]grade/i,
      /\bcertified\b/i,
      /\b(CE|UKCA)[- ]marked\b/i,
      /fully (secure|compliant|encrypted)/i,
      /100% secure/i,
    ];
    for (const re of banned) expect(texts).not.toMatch(re);
  });

  it("mentions end-to-end encryption only to say the service isn't", () => {
    for (const m of texts.matchAll(/end-to-end encrypted[^.]*/gi)) {
      const sentenceStart = texts.lastIndexOf(".", m.index) + 1;
      expect(texts.slice(sentenceStart, m.index! + m[0].length)).toMatch(/\bnot\b/i);
    }
  });

  it("never shows release-firmware or planned protections as active", () => {
    const active = items.filter((i) => i.status === "active").map((i) => `${i.title} ${i.text}`).join("\n");
    expect(active).not.toMatch(/secure boot|flash encryption|signed (software )?updates|wss:\/\//i);
  });

  it("lists the battery, charging and battery life", () => {
    for (const label of ["Battery", "Charging", "Battery life"]) expect(specs.find((s) => s.label === label)).toBeTruthy();
  });
});
