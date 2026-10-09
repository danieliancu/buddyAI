import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, Bot, Captions, Check, ChevronRight, Palette, Plus, RefreshCw, RotateCcw, Save, X } from "lucide-react";
import {
  api,
  ApiError,
  type Device,
  type DeviceSettings,
  type LanguageInfo,
  type Options,
  type MyPersona,
  type Persona,
  type SettingsPatch,
  type SettingsResponse,
  type Theme,
  type VadSensitivity,
} from "../api";
import { useLive } from "../live";
import { OnlineDot, StateBadge } from "../components/DeviceBits";
import WatchPreview from "../components/WatchPreview";
import { LanguagePicker, VoiceSampleButton } from "../components/LanguageBits";
import { primeLanguages } from "../languages";
import WatchHeader from "./my/WatchHeader";
import { Button, Card, ErrorBox, Field, PageHeader, Select, Slider, Spinner, Textarea, Toggle, cx } from "../components/ui";

const COMMON_TIMEZONES = [
  "Europe/Bucharest",
  "Europe/Chisinau",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Lisbon",
  "Europe/Madrid",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Rome",
  "Europe/Vienna",
  "Europe/Budapest",
  "Europe/Athens",
  "Europe/Istanbul",
  "Europe/Kyiv",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "America/Toronto",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "UTC",
];

/** Every IANA zone the browser knows, grouped by region (falls back to the common list). */
function allTimezones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return COMMON_TIMEZONES;
  }
}

/** "UTC+03:00" for a zone, right now (so summer time shows as it is today). */
function utcOffset(zone: string): string {
  try {
    const name = new Intl.DateTimeFormat("en-GB", { timeZone: zone, timeZoneName: "longOffset" })
      .formatToParts(new Date())
      .find((p) => p.type === "timeZoneName")?.value;
    return !name || name === "GMT" ? "UTC+00:00" : name.replace("GMT", "UTC");
  } catch {
    return "";
  }
}

function tzLabel(zone: string): string {
  const off = utcOffset(zone);
  const city = zone.includes("/") ? zone.slice(zone.indexOf("/") + 1).replace(/_/g, " ").replace(/\//g, " / ") : zone;
  return off ? `${city} (${off})` : city;
}

const TZ_GROUPS: [string, string[]][] = (() => {
  const groups = new Map<string, string[]>();
  for (const z of allTimezones()) {
    if (!z.includes("/")) continue;
    const region = z.slice(0, z.indexOf("/"));
    if (region === "Etc") continue;
    (groups.get(region) ?? groups.set(region, []).get(region)!).push(z);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
})();

/* The watch has two themes, blue ("midnight") and white ("mono"), with fixed colours. */
const THEME_BLUE = "midnight";
const THEME_WHITE = "mono";
const VAD_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High" };
/* The same setting for the customer: how long a pause ends the question (server vad.py END_SILENCE_MS). */
const PAUSE_CHOICES: [VadSensitivity, string][] = [["high", "Short"], ["medium", "Normal"], ["low", "Long"]];

/** Voice that will be used for `lang` (mirrors the server: override > default voice, within the language's voice set). */
function effectiveVoice(s: DeviceSettings, o: Options, lang: string): string {
  const override = s.tts_voice_overrides?.[lang];
  if (override) return override;
  const set = o.tts.by_language?.[lang];
  if (set) return s.tts_voice && set.voices.includes(s.tts_voice) ? s.tts_voice : set.default_voice;
  return s.tts_voice ?? o.tts.default_voice;
}

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

/** Only the fields that differ; the theme is sent as its preset (the server applies the colours). */
function diff(orig: DeviceSettings, draft: DeviceSettings): SettingsPatch {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(draft) as (keyof DeviceSettings)[]) {
    if (k === "theme") continue;
    if (!same(draft[k], orig[k])) out[k] = draft[k];
  }
  if (draft.theme.preset !== orig.theme.preset) out.theme = { preset: draft.theme.preset };
  return out as SettingsPatch;
}

function validateLocal(s: DeviceSettings): Record<string, string> {
  const out: Record<string, string> = {};
  try {
    new Intl.DateTimeFormat("en", { timeZone: s.timezone });
    if (!s.timezone) throw new Error();
  } catch {
    out.timezone = "Unknown time zone (e.g. Europe/Bucharest)";
  }
  return out;
}

interface Source {
  devices: () => Promise<Device[]>;
  settings: (id: string) => Promise<SettingsResponse>;
  patchSettings: (id: string, changes: SettingsPatch) => Promise<SettingsResponse>;
  options: () => Promise<Options>;
  personas: () => Promise<(Persona | MyPersona)[]>;
}

const SOURCES: Record<"admin" | "customer", Source> = {
  admin: {
    devices: () => api.devices.list(),
    settings: api.devices.settings,
    patchSettings: api.devices.patchSettings,
    options: api.options,
    personas: api.personas.list,
  },
  customer: {
    devices: api.me.devices.list,
    settings: api.me.devices.settings,
    patchSettings: api.me.devices.patchSettings,
    options: api.me.options,
    personas: api.me.personas.list,
  },
};

/**
 * Watch settings. The operator ("admin") sees every field; the customer view hides the technical
 * ones (model, listening time, context turns, reply length; VAD shows as "Pause before ola answers")
 * and uses the /api/me endpoints.
 */
export default function DeviceSettingsPage({ mode = "admin" }: { mode?: "admin" | "customer" }) {
  const customer = mode === "customer";
  const src = SOURCES[mode];
  const { id = "" } = useParams();
  const [device, setDevice] = useState<Device | null>(null);
  const [options, setOptions] = useState<Options | null>(null);
  const [personas, setPersonas] = useState<(Persona | MyPersona)[]>([]);
  const [orig, setOrig] = useState<DeviceSettings | null>(null);
  const [draft, setDraft] = useState<DeviceSettings | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<unknown>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [remoteChange, setRemoteChange] = useState(false);

  const loadSettings = async () => {
    const s = await src.settings(id);
    setOrig(s.settings);
    setDraft(s.settings);
    setVersion(s.version);
    setRemoteChange(false);
    setFieldErr({});
  };

  useEffect(() => {
    setLoadErr(null);
    Promise.all([src.devices(), src.options(), src.personas(), loadSettings()])
      .then(([devs, opts, pers]) => {
        setDevice(devs.find((d) => d.id === id) ?? null);
        setOptions(opts);
        primeLanguages(opts.languages);
        setPersonas(pers);
      })
      .catch(setLoadErr);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, mode]);

  const patch = useMemo(() => (orig && draft ? diff(orig, draft) : {}), [orig, draft]);
  const dirtyCount = Object.keys(patch).length;

  useLive((e) => {
    if (!("device_id" in e) || e.device_id !== id) return;
    if (e.type === "settings_changed") {
      if (dirtyCount === 0) loadSettings().catch(() => undefined);
      else setRemoteChange(true);
    } else if (e.type === "device_online" || e.type === "device_offline") {
      setDevice((d) => d && { ...d, online: e.type === "device_online", state: e.type === "device_online" ? "idle" : null });
    } else if (e.type === "device_state") {
      setDevice((d) => d && { ...d, state: e.state });
    } else if (e.type === "device_status") {
      setDevice(
        (d) =>
          d && {
            ...d,
            battery_pct: e.battery_pct ?? d.battery_pct,
            charging: e.charging ?? d.charging,
            rssi: e.rssi ?? d.rssi,
          },
      );
    }
  });

  if (loadErr) {
    return (
      <>
        <BackLink customer={customer} />
        <ErrorBox error={loadErr instanceof ApiError && loadErr.status === 404 ? new Error("Watch not found.") : loadErr} />
      </>
    );
  }
  if (!draft || !orig || !options) return <Spinner />;

  const set = <K extends keyof DeviceSettings>(k: K, v: DeviceSettings[K]) => setDraft((d) => d && { ...d, [k]: v });
  const pickPreset = (name: string) => {
    const colors = options.theme_presets[name];
    if (colors) setDraft((d) => d && { ...d, theme: { ...d.theme, preset: name, ...colors } as Theme });
  };
  const white = draft.theme.preset === THEME_WHITE;
  const err = (k: string) => fieldErr[k];

  const save = async () => {
    setSaveErr(null);
    // Client-side checks: the server answers 500 (not 422) for invalid colors / time zones.
    const local = validateLocal(draft);
    setFieldErr(local);
    if (Object.keys(local).length) {
      setSaveErr(new Error("Some values are invalid. Check the highlighted fields."));
      return;
    }
    setSaving(true);
    try {
      const r = await src.patchSettings(id, patch);
      setOrig(r.settings);
      setDraft(r.settings);
      setVersion(r.version);
      setSavedAt(Date.now());
      setRemoteChange(false);
    } catch (e) {
      if (e instanceof ApiError && e.fieldErrors.length) {
        const map: Record<string, string> = {};
        for (const fe of e.fieldErrors) map[fe.loc.join(".")] = fe.msg;
        setFieldErr(map);
        setSaveErr(new Error("Some values are invalid. Check the highlighted fields."));
      } else setSaveErr(e);
    } finally {
      setSaving(false);
    }
  };

  const languages: LanguageInfo[] = Array.isArray(options.languages) ? options.languages : [];
  const langInfo = (code: string | null | undefined) => languages.find((l) => l.code === code);
  const selectedLang = draft.language === "auto" ? null : langInfo(draft.language);
  // Language used for the "Play sample" button: the fixed language, else the preferred one, else English.
  const sampleLang = draft.language !== "auto" ? draft.language : draft.preferred_language || "en";
  const sampleLangName = langInfo(sampleLang)?.name ?? sampleLang.toUpperCase();
  const overrides = draft.tts_voice_overrides ?? {};
  const setOverrides = (next: Record<string, string>) => set("tts_voice_overrides", next);

  const preview = (
    <WatchPreview
      theme={draft.theme}
      language={draft.language}
      preferredLanguage={draft.preferred_language}
      timezone={draft.timezone}
      brightness={draft.brightness}
    />
  );

  const savedNote = savedAt && dirtyCount === 0 && (
    <span className="inline-flex items-center gap-1 text-ok">
      <Check className="size-3.5" /> Saved{device?.online ? " and sent to the watch" : ""}
    </span>
  );
  const defaultPersona = personas.find((p) => p.is_default);
  const ownPersonas = personas.filter((p) => "own" in p && p.own);
  const systemPersonas = personas.filter((p) => !ownPersonas.includes(p));

  return (
    <>
      {customer ? (
        <WatchHeader id={id} device={device} active="settings" note={savedNote || undefined} onRenamed={(name) => setDevice((d) => d && { ...d, name })} />
      ) : (
        <>
          <BackLink customer={false} />
          <PageHeader
        title={device?.name ?? id}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            {device && <OnlineDot online={device.online} />}
            {device && <StateBadge online={device.online} state={device.state} />}
            <span>Settings • version {version}</span>
            {savedNote}
          </span>
        }
          />
        </>
      )}

      {remoteChange && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-warn/30 bg-warn-bg px-4 py-3 text-sm text-warn">
          <span className="flex-1">Settings were changed on the watch. Reload to see the new values (local edits will be lost).</span>
          <Button size="sm" variant="secondary" icon={<RefreshCw className="size-3.5" />} onClick={() => loadSettings()}>
            Reload
          </Button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-6">
          {/* ---------------- AI ---------------- */}
          <Card title={<SectionTitle icon={<Bot className="size-4" />}>{customer ? "Voice & language" : "AI"}</SectionTitle>}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Language"
                error={err("language")}
                hint={<CaptionsNote lang={selectedLang} auto={draft.language === "auto"} fallback="Voice and assistant language" />}
              >
                <LanguagePicker
                  ariaLabel="Language"
                  value={draft.language}
                  onChange={(v) => {
                    const lang = v ?? "auto";
                    set("language", lang);
                    // The preferred language (quick settings, "auto" fallback) follows the chosen one.
                    if (lang !== "auto") set("preferred_language", lang);
                  }}
                  languages={languages}
                  autoLabel="Auto — reply in the language you speak"
                  invalid={!!err("language")}
                />
              </Field>
              <Field
                label="Persona"
                error={err("persona_id")}
                hint={
                  <Link to={customer ? "/my/personas" : "/admin/personas"} className="underline">
                    {customer ? "Create your own personas" : "Manage personas"}
                  </Link>
                }
              >
                <Select
                  value={draft.persona_id ?? ""}
                  onChange={(e) => set("persona_id", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Default ({defaultPersona?.name ?? "—"})</option>
                  {ownPersonas.length > 0 ? (
                    <>
                      <optgroup label="ola personas">
                        {systemPersonas.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </optgroup>
                      <optgroup label="My personas">
                        {ownPersonas.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </optgroup>
                    </>
                  ) : (
                    personas.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))
                  )}
                </Select>
              </Field>
              <Field label="Custom instructions" className="sm:col-span-2" error={err("custom_instructions")} hint={`${draft.custom_instructions.length}/2000`}>
                <Textarea
                  maxLength={2000}
                  rows={3}
                  placeholder="E.g. My name is Ana. Call me by my name and keep answers short."
                  value={draft.custom_instructions}
                  onChange={(e) => set("custom_instructions", e.target.value)}
                />
              </Field>
              {!customer && (
              <>
              <Field label="LLM model" error={err("llm_model")}>
                <Select value={draft.llm_model ?? ""} onChange={(e) => set("llm_model", e.target.value || null)}>
                  <option value="">Default (server)</option>
                  {options.llm_models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </Select>
              </Field>
              </>
              )}
              <Field
                label={customer ? "Pause before ola answers" : "VAD sensitivity"}
                error={err("vad_sensitivity")}
                hint={customer ? "How long ola waits when you pause mid-sentence" : "How eagerly the end of speech is detected"}
              >
                <div className="flex gap-2">
                  {(customer ? PAUSE_CHOICES : options.vad_sensitivity.map((v): [VadSensitivity, string] => [v, VAD_LABEL[v] ?? v])).map(([v, label]) => (
                    <Chip key={v} active={draft.vad_sensitivity === v} onClick={() => set("vad_sensitivity", v)}>
                      {label}
                    </Chip>
                  ))}
                </div>
              </Field>
              <Field label="Voice" error={err("tts_voice")} hint={`Sample in ${sampleLangName}`}>
                <div className="flex gap-2">
                  <Select className="min-w-0 flex-1" value={draft.tts_voice ?? ""} onChange={(e) => set("tts_voice", e.target.value || null)}>
                    <option value="">Default voice ({options.tts.default_voice})</option>
                    {(options.tts.voices ?? []).map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                  <VoiceSampleButton voice={effectiveVoice(draft, options, sampleLang)} language={sampleLang} />
                </div>
              </Field>
              <div className="sm:col-span-2">
                <VoiceOverrides
                  overrides={overrides}
                  onChange={setOverrides}
                  options={options}
                  languages={languages}
                  settings={draft}
                  err={err}
                />
              </div>
              <Field label={customer ? "Speaking speed" : "Speech rate"} error={err("speech_rate")}>
                <Slider value={draft.speech_rate} min={0.5} max={2} step={0.05} onChange={(v) => set("speech_rate", v)} format={(v) => `${v.toFixed(2)}×`} />
              </Field>
              <Field label="Wait for speech" error={err("wait_for_speech_s")} hint="How long the mic waits for the first word. Silence is free: nothing is sent for transcription until you speak">
                <Slider value={draft.wait_for_speech_s} min={5} max={60} onChange={(v) => set("wait_for_speech_s", v)} format={(v) => `${v} s`} />
              </Field>
              {!customer && (
              <>
              <Field label="Max reply length" error={err("max_reply_chars")}>
                <Slider value={draft.max_reply_chars} min={80} max={2000} step={20} onChange={(v) => set("max_reply_chars", v)} format={(v) => `${v} chars`} />
              </Field>
              <Field label="History turns" error={err("history_turns")} hint="Previous turns sent to the AI as context">
                <Slider value={draft.history_turns} min={0} max={30} onChange={(v) => set("history_turns", v)} format={(v) => `${v} turns`} />
              </Field>
              <Field label="Max listening time" error={err("max_listen_s")} hint="Longest question, from the first word">
                <Slider value={draft.max_listen_s} min={3} max={60} onChange={(v) => set("max_listen_s", v)} format={(v) => `${v} s`} />
              </Field>
              <Field label="Web search" error={err("web_search")} hint="Weather, news, addresses, opening hours… Each search costs about 1p">
                <Toggle checked={draft.web_search} onChange={(v) => set("web_search", v)} label="Let the AI search the internet" />
              </Field>
              </>
              )}
            </div>
          </Card>

          {/* ---------------- Aspect ---------------- */}
          <Card title={<SectionTitle icon={<Palette className="size-4" />}>Appearance</SectionTitle>}>
            <div className="mb-6 lg:hidden">{preview}</div>
            <div className="space-y-5">
              <Field label="Theme" error={err("theme")}>
                <div className="inline-flex items-center gap-3 text-sm select-none">
                  <button type="button" onClick={() => pickPreset(THEME_BLUE)} className={cx("flex items-center gap-1.5", white ? "text-muted" : "font-medium")}>
                    <span className="size-3.5 rounded-full border border-border" style={{ background: options.theme_presets[THEME_BLUE]?.accent }} />
                    Blue
                  </button>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={white}
                    aria-label="White theme"
                    onClick={() => pickPreset(white ? THEME_BLUE : THEME_WHITE)}
                    className="relative h-6 w-11 shrink-0 rounded-full border border-border bg-surface-2 transition"
                  >
                    <span
                      className={cx("absolute top-0.5 size-[18px] rounded-full shadow transition-all", white ? "left-[22px]" : "left-0.5")}
                      style={{ background: options.theme_presets[white ? THEME_WHITE : THEME_BLUE]?.accent }}
                    />
                  </button>
                  <button type="button" onClick={() => pickPreset(THEME_WHITE)} className={cx("flex items-center gap-1.5", white ? "font-medium" : "text-muted")}>
                    <span className="size-3.5 rounded-full border border-border" style={{ background: options.theme_presets[THEME_WHITE]?.accent }} />
                    White
                  </button>
                </div>
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Brightness" error={err("brightness")}>
                  <Slider value={draft.brightness} min={5} max={100} onChange={(v) => set("brightness", v)} format={(v) => `${v}%`} />
                </Field>
                <Field label="Volume" error={err("volume")}>
                  <Slider value={draft.volume} min={0} max={100} onChange={(v) => set("volume", v)} format={(v) => `${v}%`} />
                </Field>
                <Field label="Screen timeout" error={err("screen_timeout_s")}>
                  <Slider value={draft.screen_timeout_s} min={5} max={300} step={5} onChange={(v) => set("screen_timeout_s", v)} format={(v) => `${v} s`} />
                </Field>
                <Field label="Time zone" error={err("timezone")}>
                  <Select value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} className="h-6! py-0 text-xs">
                    {![...COMMON_TIMEZONES, ...TZ_GROUPS.flatMap(([, z]) => z)].includes(draft.timezone) && (
                      <option value={draft.timezone}>{draft.timezone}</option>
                    )}
                    <optgroup label="Common">
                      {COMMON_TIMEZONES.map((z) => (
                        <option key={z} value={z}>
                          {z === "UTC" ? "UTC" : `${z.split("/")[0]} · ${tzLabel(z)}`}
                        </option>
                      ))}
                    </optgroup>
                    {TZ_GROUPS.map(([region, zones]) => (
                      <optgroup key={region} label={region}>
                        {zones.map((z) => (
                          <option key={`${region}-${z}`} value={z}>
                            {tzLabel(z)}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </Select>
                </Field>
              </div>
            </div>
          </Card>
        </div>

        <aside className="hidden lg:block">
          <div className="sticky top-8 space-y-3">
            <p className="text-center text-xs text-muted">Live preview · 410×502</p>
            {preview}
          </div>
        </aside>
      </div>

      {/* save bar */}
      <div style={{ bottom: "var(--bottom-nav, 0px)" }} className="sticky z-20 -mx-4 mt-6 border-t border-border bg-bg/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <span className="min-w-0 flex-1 text-sm text-muted">
            {dirtyCount ? `${dirtyCount} unsaved ${dirtyCount === 1 ? "change" : "changes"}` : "No changes"}
          </span>
          <Button variant="ghost" icon={<RotateCcw className="size-4" />} disabled={!dirtyCount || saving} onClick={() => { setDraft(orig); setFieldErr({}); setSaveErr(null); }}>
            Discard
          </Button>
          <Button variant="primary" icon={<Save className="size-4" />} loading={saving} disabled={!dirtyCount} onClick={save}>
            Save
          </Button>
        </div>
        {!!saveErr && (
          <div className="mt-2">
            <ErrorBox error={saveErr} />
          </div>
        )}
      </div>
    </>
  );
}

function BackLink({ customer }: { customer: boolean }) {
  return (
    <Link to={customer ? "/my" : "/admin/devices"} className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
      <ArrowLeft className="size-4" /> {customer ? "My watches" : "Devices"}
    </Link>
  );
}

function SectionTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className="text-accent">{icon}</span>
      {children}
    </span>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "h-10 flex-1 rounded-lg border px-3 text-sm whitespace-nowrap transition",
        active ? "border-accent bg-accent-bg font-medium text-accent" : "border-border text-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

/** What the chosen language means for the watch: the voice, the captions and the watch's own menus. */
function CaptionsNote({ lang, auto, fallback }: { lang: LanguageInfo | null | undefined; auto?: boolean; fallback: string }) {
  if (lang && !lang.captions) {
    return (
      <span className="inline-flex items-start gap-1 text-warn">
        <Captions className="mt-px size-3.5 shrink-0" />
        The watch will speak this language but can't display its text. Its menus stay in English.
      </span>
    );
  }
  if (auto) {
    return <>{fallback}. The watch menus follow the language you last spoke (English if they aren't translated into it).</>;
  }
  if (lang) {
    return (
      <>
        {fallback}.{" "}
        {lang.watch_menus === false
          ? "The watch menus stay in English (not translated into this language yet)."
          : `The watch menus and dates are in ${lang.name} too.`}
      </>
    );
  }
  return <>{fallback}</>;
}

/** Optional per-language voice (tts_voice_overrides), collapsed unless something is set. */
function VoiceOverrides({
  overrides,
  onChange,
  options,
  languages,
  settings,
  err,
}: {
  overrides: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  options: Options;
  languages: LanguageInfo[];
  settings: DeviceSettings;
  err: (k: string) => string | undefined;
}) {
  const entries = Object.entries(overrides);
  const [open, setOpen] = useState(entries.length > 0);
  const [adding, setAdding] = useState(false);
  const byLang = options.tts.by_language ?? {};
  const special = Object.keys(byLang);
  const name = (code: string) => languages.find((l) => l.code === code)?.name ?? code.toUpperCase();
  const voicesFor = (code: string) => byLang[code]?.voices ?? options.tts.voices ?? [];
  const defaultFor = (code: string) => effectiveVoice({ ...settings, tts_voice_overrides: {} }, options, code);

  // Open automatically when overrides appear (e.g. after a reload).
  useEffect(() => {
    if (entries.length) setOpen(true);
  }, [entries.length]);

  const setVoice = (code: string, voice: string) => onChange({ ...overrides, [code]: voice });
  const remove = (code: string) => {
    const next = { ...overrides };
    delete next[code];
    onChange(next);
  };
  const add = (code: string | null) => {
    setAdding(false);
    if (!code || code === "auto" || overrides[code]) return;
    onChange({ ...overrides, [code]: defaultFor(code) });
  };

  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm"
      >
        <ChevronRight className={cx("size-4 text-muted transition-transform", open && "rotate-90")} />
        <span className="font-medium">Per-language voice</span>
        <span className="text-xs text-muted">
          {entries.length ? `${entries.length} ${entries.length === 1 ? "language" : "languages"}` : "optional"}
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <p className="text-xs text-muted">
            Use a different voice for specific languages. Other languages use the voice above.
            {special.length > 0 && <> Languages with their own voice set: {special.map(name).join(", ")}.</>}
          </p>
          {entries.map(([code, voice]) => {
            const voices = voicesFor(code);
            const e = err(`tts_voice_overrides.${code}`);
            return (
              <div key={code} className="flex flex-col gap-1">
                <div className="flex items-start gap-1.5 sm:gap-2">
                  <span className="flex h-10 w-20 shrink-0 items-center truncate text-sm sm:w-32" title={name(code)}>
                    {name(code)}
                  </span>
                  <Select
                    aria-label={`Voice for ${name(code)}`}
                    className="min-w-0 flex-1"
                    value={voice}
                    onChange={(ev) => setVoice(code, ev.target.value)}
                  >
                    {!voices.includes(voice) && <option value={voice}>{voice}</option>}
                    {voices.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                  <VoiceSampleButton voice={voice} language={code} label="Play" compact />
                  <Button variant="ghost" icon={<X className="size-4" />} aria-label={`Remove ${name(code)}`} onClick={() => remove(code)} />
                </div>
                {e && <p className="text-xs text-danger">{e}</p>}
              </div>
            );
          })}
          {err("tts_voice_overrides") && <p className="text-xs text-danger">{err("tts_voice_overrides")}</p>}
          {adding ? (
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <LanguagePicker
                  ariaLabel="Add language"
                  value={null}
                  onChange={add}
                  languages={languages}
                  noneLabel="Choose a language…"
                  exclude={Object.keys(overrides)}
                />
              </div>
              <Button variant="ghost" onClick={() => setAdding(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)} disabled={!languages.length}>
              Add language
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
