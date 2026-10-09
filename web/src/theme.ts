import { useEffect, useState } from "react";

/** The customer's colour theme, kept in this browser. "system" follows the device (prefers-color-scheme). */
export type ThemePref = "system" | "light" | "dark";

/** Also read by the inline script in index.html (applies the theme before the first paint). */
export const THEME_KEY = "ola-theme";
const CHANGED = "ola-theme-changed";

export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): "light" | "dark" {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Sets html[data-theme] to the theme actually shown (index.css: dark by default, light tokens on "light"). */
export function applyTheme(pref: ThemePref = getThemePref()) {
  document.documentElement.dataset.theme = pref === "system" ? systemTheme() : pref;
}

export function setThemePref(pref: ThemePref) {
  try {
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    // storage blocked (private mode): the choice still applies until the page is reloaded
  }
  applyTheme(pref);
  window.dispatchEvent(new CustomEvent(CHANGED, { detail: pref }));
}

/** Keeps "system" in step with the device switching between light and dark. Call once at start-up. */
export function startThemeSync() {
  applyTheme();
  window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", () => {
    if (getThemePref() === "system") applyTheme("system");
  });
}

export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(getThemePref);
  useEffect(() => {
    const on = (e: Event) => setPref((e as CustomEvent<ThemePref>).detail);
    window.addEventListener(CHANGED, on);
    return () => window.removeEventListener(CHANGED, on);
  }, []);
  return [pref, setThemePref];
}
