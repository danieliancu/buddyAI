// Language registry from GET /api/options (loaded once, shared by all pages).
import { useEffect, useState } from "react";
import { api, type LanguageInfo } from "./api";

export type LanguageMap = Map<string, LanguageInfo>;

let cache: Promise<LanguageMap> | null = null;
let loaded: LanguageMap | null = null;

export function loadLanguages(): Promise<LanguageMap> {
  cache ??= api
    .options()
    .then((o) => {
      loaded = new Map((o.languages ?? []).map((l) => [l.code, l]));
      return loaded;
    })
    .catch(() => {
      cache = null; // retry on next use
      return new Map<string, LanguageInfo>();
    });
  return cache;
}

/** Seed the registry from an options response that was already fetched. */
export function primeLanguages(list: LanguageInfo[] | undefined): void {
  if (!list?.length) return;
  loaded = new Map(list.map((l) => [l.code, l]));
  cache = Promise.resolve(loaded);
}

/** Language map (empty until loaded; the component re-renders once it arrives). */
export function useLanguages(): LanguageMap {
  const [map, setMap] = useState<LanguageMap>(() => loaded ?? new Map());
  useEffect(() => {
    let alive = true;
    loadLanguages().then((m) => alive && setMap(m));
    return () => {
      alive = false;
    };
  }, []);
  return map;
}
