import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { applyTheme, getThemePref, setThemePref, startThemeSync, THEME_KEY, useThemePref } from "./theme";

// A device in light or dark mode (jsdom has no matchMedia).
let deviceLight = false;
let listeners: (() => void)[] = [];
function setDevice(light: boolean) {
  deviceLight = light;
  listeners.forEach((l) => l());
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  deviceLight = false;
  listeners = [];
  window.matchMedia = ((q: string) => ({
    get matches() {
      return q.includes("light") && deviceLight;
    },
    addEventListener: (_: string, l: () => void) => listeners.push(l),
  })) as unknown as typeof window.matchMedia;
});

afterEach(() => localStorage.clear());

const shown = () => document.documentElement.dataset.theme;

describe("customer theme: light / dark / system", () => {
  it("defaults to system, which follows the device", () => {
    expect(getThemePref()).toBe("system");
    applyTheme();
    expect(shown()).toBe("dark");
    setDevice(true);
    applyTheme();
    expect(shown()).toBe("light");
  });

  it("a chosen theme wins over the device and is kept in this browser", () => {
    setDevice(true);
    setThemePref("dark");
    expect(shown()).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(getThemePref()).toBe("dark");
    setThemePref("light");
    setDevice(false);
    expect(shown()).toBe("light");
  });

  it("system forgets the choice and tracks the device switching", () => {
    setThemePref("light");
    setThemePref("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    startThemeSync();
    expect(shown()).toBe("dark");
    setDevice(true);
    expect(shown()).toBe("light");
  });

  it("the device switching does not override a chosen theme", () => {
    setThemePref("dark");
    startThemeSync();
    setDevice(true);
    expect(shown()).toBe("dark");
  });

  it("an unknown stored value counts as system", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getThemePref()).toBe("system");
  });

  it("the hook updates when the theme is changed", () => {
    const { result } = renderHook(() => useThemePref());
    expect(result.current[0]).toBe("system");
    act(() => result.current[1]("light"));
    expect(result.current[0]).toBe("light");
    expect(shown()).toBe("light");
  });

  it("the early script in public/ applies the same rule before the app loads", () => {
    const script = readFileSync(resolve(__dirname, "../public/theme-init.js"), "utf8");
    const run = () => new Function(script)();
    run();
    expect(shown()).toBe("dark");
    setDevice(true);
    run();
    expect(shown()).toBe("light");
    localStorage.setItem(THEME_KEY, "dark");
    run();
    expect(shown()).toBe("dark");
  });
});
