import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown, Play, Square } from "lucide-react";
import { api, ApiError, type LanguageInfo } from "../api";
import { useArea } from "../area";
import { Button, cx } from "./ui";

// ---------- searchable language select ----------

type Item = { value: string; label: string; native?: string; rtl?: boolean; search: string };

const norm = (s: string) =>
  s
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase();

/**
 * Combobox: type to filter by English or native name (or code). `value === null` is the "none" entry,
 * "auto" the Auto entry when `autoLabel` is given.
 */
export function LanguagePicker({
  value,
  onChange,
  languages,
  autoLabel,
  noneLabel,
  exclude,
  id,
  ariaLabel,
  invalid,
}: {
  value: string | null;
  onChange: (v: string | null) => void;
  languages: LanguageInfo[];
  /** Adds an "auto" option first. */
  autoLabel?: string;
  /** Adds a "none" (null) option first. */
  noneLabel?: string;
  /** Codes to hide (e.g. languages that already have an override). */
  exclude?: string[];
  id?: string;
  ariaLabel?: string;
  invalid?: boolean;
}) {
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    if (noneLabel) out.push({ value: "", label: noneLabel, search: norm(noneLabel) });
    if (autoLabel) out.push({ value: "auto", label: autoLabel, search: norm(`auto ${autoLabel}`) });
    for (const l of languages) {
      if (exclude?.includes(l.code) && l.code !== value) continue;
      out.push({
        value: l.code,
        label: l.name,
        native: l.native_name !== l.name ? l.native_name : undefined,
        rtl: l.rtl,
        search: norm(`${l.name} ${l.native_name} ${l.code}`),
      });
    }
    // Keep an unknown current value selectable (e.g. a code the server no longer lists).
    if (value && value !== "auto" && !out.some((i) => i.value === value)) {
      out.push({ value, label: value.toUpperCase(), search: norm(value) });
    }
    return out;
  }, [languages, autoLabel, noneLabel, exclude, value]);

  const filtered = useMemo(() => {
    const q = norm(query.trim());
    if (!q) return items;
    // Prefix matches first, then substring matches.
    const starts = items.filter((i) => i.search.split(" ").some((w) => w.startsWith(q)));
    const rest = items.filter((i) => !starts.includes(i) && i.search.includes(q));
    return [...starts, ...rest];
  }, [items, query]);

  const current = items.find((i) => i.value === (value ?? ""));
  const display = current ? (current.native ? `${current.label} · ${current.native}` : current.label) : "";

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Keep the highlighted option visible.
  useEffect(() => {
    if (!open) return;
    list.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const openList = () => {
    setQuery("");
    const idx = items.findIndex((i) => i.value === (value ?? ""));
    setActive(Math.max(0, idx));
    setOpen(true);
  };

  const pick = (it: Item) => {
    onChange(it.value === "" ? null : it.value);
    setOpen(false);
    setQuery("");
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      e.preventDefault();
      openList();
      return;
    }
    if (!open) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(filtered.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const it = filtered[active];
      if (it) pick(it);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      setQuery("");
    } else if (e.key === "Tab") {
      setOpen(false);
      setQuery("");
    }
  };

  return (
    <div ref={box} className="relative">
      <input
        ref={input}
        id={id}
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={invalid || undefined}
        autoComplete="off"
        spellCheck={false}
        value={open ? query : display}
        placeholder={open ? display || "Search languages…" : "Select…"}
        onFocus={() => !open && openList()}
        onClick={() => !open && openList()}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
          if (!open) setOpen(true);
        }}
        onKeyDown={onKey}
        className={cx(
          "h-10 w-full rounded-lg border bg-bg pr-8 pl-3 text-sm text-fg placeholder:text-muted/70",
          "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30",
          invalid ? "border-danger" : "border-border",
        )}
      />
      <ChevronDown
        className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted"
        aria-hidden
      />
      {open && (
        <ul
          ref={list}
          id={listId}
          role="listbox"
          className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-border bg-surface py-1 text-sm shadow-lg"
        >
          {filtered.length === 0 && <li className="px-3 py-2 text-muted">No matching language</li>}
          {filtered.map((it, i) => {
            const selected = it.value === (value ?? "");
            return (
              <li
                key={it.value || "__none"}
                data-idx={i}
                role="option"
                aria-selected={selected}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(it)}
                className={cx(
                  "flex cursor-pointer items-center gap-2 px-3 py-1.5",
                  i === active && "bg-surface-2",
                  selected && "font-medium text-accent",
                )}
              >
                <span className="min-w-0 flex-1 truncate">
                  {it.label}
                  {it.native && (
                    <span className="ml-2 text-muted" dir={it.rtl ? "rtl" : "auto"}>
                      {it.native}
                    </span>
                  )}
                </span>
                {it.value && it.value !== "auto" && (
                  <span className="font-mono text-[11px] text-muted uppercase">{it.value}</span>
                )}
                {selected && <Check className="size-3.5 shrink-0" />}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ---------- voice sample ----------

// One sample plays at a time across the page.
let stopCurrent: (() => void) | null = null;

export function VoiceSampleButton({
  voice,
  language,
  label = "Play sample",
  size = "md",
  disabled,
}: {
  voice: string;
  language: string;
  label?: string;
  size?: "sm" | "md";
  disabled?: boolean;
}) {
  const area = useArea();
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const reqRef = useRef(0);

  // Stop and release on unmount.
  useEffect(
    () => () => {
      reqRef.current++;
      stopRef.current?.();
    },
    [],
  );

  // Changing voice/language invalidates an error message.
  useEffect(() => setError(null), [voice, language]);

  const stop = () => stopRef.current?.();

  const play = async () => {
    stopCurrent?.();
    setError(null);
    setState("loading");
    const req = ++reqRef.current;
    try {
      const blob = await (area === "me" ? api.me.voiceSample : api.voiceSample)(voice, language);
      if (req !== reqRef.current) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      const cleanup = () => {
        audio.pause();
        URL.revokeObjectURL(url);
        if (stopCurrent === cleanup) stopCurrent = null;
        stopRef.current = null;
        setState("idle");
      };
      stopRef.current = cleanup;
      stopCurrent = cleanup;
      audio.onended = cleanup;
      audio.onerror = () => {
        cleanup();
        setError("The sample could not be played.");
      };
      setState("playing");
      await audio.play();
    } catch (e) {
      if (req !== reqRef.current) return;
      stopRef.current?.();
      setState("idle");
      setError(
        e instanceof ApiError
          ? e.message
          : e instanceof Error && e.name === "NotAllowedError"
            ? "The browser blocked audio playback."
            : "The sample could not be played.",
      );
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-1">
      {state === "playing" ? (
        <Button size={size} icon={<Square className="size-3.5" />} onClick={stop}>
          Stop
        </Button>
      ) : (
        <Button size={size} icon={<Play className="size-3.5" />} loading={state === "loading"} disabled={disabled || !voice} onClick={play}>
          {state === "loading" ? "Loading…" : label}
        </Button>
      )}
      {error && <p className="text-xs text-danger" role="alert">{error}</p>}
    </div>
  );
}
