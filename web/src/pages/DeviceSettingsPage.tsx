import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, Bot, Check, Palette, RefreshCw, RotateCcw, Save } from "lucide-react";
import {
  api,
  ApiError,
  type Device,
  type DeviceSettings,
  type Options,
  type Persona,
  type SettingsPatch,
  type Theme,
} from "../api";
import { useLive } from "../live";
import { OnlineDot, StateBadge } from "../components/DeviceBits";
import WatchPreview from "../components/WatchPreview";
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select, Slider, Spinner, Textarea, Toggle, cx } from "../components/ui";

const TIMEZONES = [
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

const THEME_KEYS = ["accent", "background", "clock", "text"] as const;
const COLOR_LABEL: Record<(typeof THEME_KEYS)[number], string> = {
  accent: "Accent",
  background: "Background",
  clock: "Clock",
  text: "Text",
};
const PRESET_LABEL: Record<string, string> = {
  midnight: "Midnight",
  ocean: "Ocean",
  forest: "Forest",
  sunset: "Sunset",
  mono: "Mono",
  custom: "Custom",
};
const VAD_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High" };
const LANGUAGE_LABEL: Record<string, string> = { ro: "Romanian", en: "English" };

/** Only the fields that differ; theme is diffed per key (the server merges partial themes). */
function diff(orig: DeviceSettings, draft: DeviceSettings): SettingsPatch {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(draft) as (keyof DeviceSettings)[]) {
    if (k === "theme") continue;
    if (draft[k] !== orig[k]) out[k] = draft[k];
  }
  const theme: Partial<Theme> = {};
  for (const k of ["preset", ...THEME_KEYS] as (keyof Theme)[]) {
    if (draft.theme[k].toUpperCase() !== orig.theme[k].toUpperCase()) theme[k] = draft.theme[k];
  }
  // If only colors changed, still send the preset so the server never re-applies preset colors.
  if (Object.keys(theme).length) out.theme = { ...theme, preset: draft.theme.preset };
  return out as SettingsPatch;
}

function validateLocal(s: DeviceSettings): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of THEME_KEYS) if (!/^#[0-9A-Fa-f]{6}$/.test(s.theme[k])) out[`theme.${k}`] = "Use #RRGGBB";
  try {
    new Intl.DateTimeFormat("en", { timeZone: s.timezone });
    if (!s.timezone) throw new Error();
  } catch {
    out.timezone = "Unknown time zone (e.g. Europe/Bucharest)";
  }
  return out;
}

export default function DeviceSettingsPage() {
  const { id = "" } = useParams();
  const [device, setDevice] = useState<Device | null>(null);
  const [options, setOptions] = useState<Options | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
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
    const s = await api.devices.settings(id);
    setOrig(s.settings);
    setDraft(s.settings);
    setVersion(s.version);
    setRemoteChange(false);
    setFieldErr({});
  };

  useEffect(() => {
    setLoadErr(null);
    Promise.all([api.devices.list(), api.options(), api.personas.list(), loadSettings()])
      .then(([devs, opts, pers]) => {
        setDevice(devs.find((d) => d.id === id) ?? null);
        setOptions(opts);
        setPersonas(pers);
      })
      .catch(setLoadErr);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

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
    }
  });

  if (loadErr) {
    return (
      <>
        <BackLink />
        <ErrorBox error={loadErr instanceof ApiError && loadErr.status === 404 ? new Error("Watch not found.") : loadErr} />
      </>
    );
  }
  if (!draft || !orig || !options) return <Spinner />;

  const set = <K extends keyof DeviceSettings>(k: K, v: DeviceSettings[K]) => setDraft((d) => d && { ...d, [k]: v });
  const setTheme = (t: Partial<Theme>) => setDraft((d) => d && { ...d, theme: { ...d.theme, ...t } });
  const pickPreset = (name: string) => {
    const colors = options.theme_presets[name];
    if (colors) setTheme({ preset: name, ...colors });
  };
  const setColor = (k: (typeof THEME_KEYS)[number], v: string) => {
    const next = { ...draft.theme, [k]: v.toUpperCase() };
    const match = Object.entries(options.theme_presets).find(([, c]) =>
      THEME_KEYS.every((key) => c[key].toUpperCase() === next[key].toUpperCase()),
    );
    setTheme({ [k]: v.toUpperCase(), preset: match ? match[0] : "custom" });
  };
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
      const r = await api.devices.patchSettings(id, patch);
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

  const ttsRo = options.tts.ro;
  const ttsEn = options.tts.en;

  const preview = (
    <WatchPreview
      theme={draft.theme}
      language={draft.language}
      time24h={draft.time_24h}
      timezone={draft.timezone}
      brightness={draft.brightness}
    />
  );

  return (
    <>
      <BackLink />
      <PageHeader
        title={device?.name ?? id}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            {device && <OnlineDot online={device.online} />}
            {device && <StateBadge online={device.online} state={device.state} />}
            <span>Settings • version {version}</span>
            {savedAt && dirtyCount === 0 && (
              <span className="inline-flex items-center gap-1 text-ok">
                <Check className="size-3.5" /> Saved{device?.online ? " and sent to the watch" : ""}
              </span>
            )}
          </span>
        }
      />

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
          <Card title={<SectionTitle icon={<Bot className="size-4" />}>AI</SectionTitle>}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Language" error={err("language")} hint="Voice and assistant language">
                <div className="flex gap-2">
                  {options.languages.map((l) => (
                    <Chip key={l.id} active={draft.language === l.id} onClick={() => set("language", l.id)}>
                      {l.id.toUpperCase()} · {LANGUAGE_LABEL[l.id] ?? l.label}
                    </Chip>
                  ))}
                </div>
              </Field>
              <Field label="Persona" error={err("persona_id")} hint={<Link to="/personas" className="underline">Manage personas</Link>}>
                <Select
                  value={draft.persona_id ?? ""}
                  onChange={(e) => set("persona_id", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Default ({personas.find((p) => p.is_default)?.name ?? "—"})</option>
                  {personas.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
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
              <Field label="VAD sensitivity" error={err("vad_sensitivity")} hint="How eagerly the end of speech is detected">
                <div className="flex gap-2">
                  {options.vad_sensitivity.map((v) => (
                    <Chip key={v} active={draft.vad_sensitivity === v} onClick={() => set("vad_sensitivity", v)}>
                      {VAD_LABEL[v] ?? v}
                    </Chip>
                  ))}
                </div>
              </Field>
              {ttsRo && (
                <Field label="Romanian voice" error={err("tts_voice_ro")}>
                  <Select value={draft.tts_voice_ro ?? ""} onChange={(e) => set("tts_voice_ro", e.target.value || null)}>
                    <option value="">Default ({ttsRo.default_voice})</option>
                    {ttsRo.voices.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              {ttsEn && (
                <Field label="English voice" error={err("tts_voice_en")}>
                  <Select value={draft.tts_voice_en ?? ""} onChange={(e) => set("tts_voice_en", e.target.value || null)}>
                    <option value="">Default ({ttsEn.default_voice})</option>
                    {ttsEn.voices.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </Select>
                </Field>
              )}
              <Field label="Speech rate" error={err("speech_rate")}>
                <Slider value={draft.speech_rate} min={0.5} max={2} step={0.05} onChange={(v) => set("speech_rate", v)} format={(v) => `${v.toFixed(2)}×`} />
              </Field>
              <Field label="Max reply length" error={err("max_reply_chars")}>
                <Slider value={draft.max_reply_chars} min={80} max={2000} step={20} onChange={(v) => set("max_reply_chars", v)} format={(v) => `${v} chars`} />
              </Field>
              <Field label="History turns" error={err("history_turns")} hint="Previous turns sent to the AI as context">
                <Slider value={draft.history_turns} min={0} max={30} onChange={(v) => set("history_turns", v)} format={(v) => `${v} turns`} />
              </Field>
              <Field label="Max listening time" error={err("max_listen_s")}>
                <Slider value={draft.max_listen_s} min={3} max={60} onChange={(v) => set("max_listen_s", v)} format={(v) => `${v} s`} />
              </Field>
            </div>
          </Card>

          {/* ---------------- Aspect ---------------- */}
          <Card title={<SectionTitle icon={<Palette className="size-4" />}>Appearance</SectionTitle>}>
            <div className="mb-6 lg:hidden">{preview}</div>
            <div className="space-y-5">
              <Field label="Theme" error={err("theme.preset")}>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(options.theme_presets).map(([name, c]) => (
                    <button
                      key={name}
                      type="button"
                      onClick={() => pickPreset(name)}
                      className={cx(
                        "flex items-center gap-2 rounded-full border py-1 pr-3 pl-1 text-sm transition",
                        draft.theme.preset === name ? "border-accent bg-accent-bg" : "border-border hover:bg-surface-2",
                      )}
                    >
                      <span className="relative size-6 overflow-hidden rounded-full border border-border" style={{ background: c.background }}>
                        <span className="absolute inset-1.5 rounded-full" style={{ background: c.accent }} />
                      </span>
                      {PRESET_LABEL[name] ?? name}
                    </button>
                  ))}
                  {!(draft.theme.preset in options.theme_presets) && (
                    <span className="flex items-center rounded-full border border-accent bg-accent-bg px-3 py-1 text-sm">
                      {PRESET_LABEL[draft.theme.preset] ?? draft.theme.preset}
                    </span>
                  )}
                </div>
              </Field>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {THEME_KEYS.map((k) => (
                  <Field key={k} label={COLOR_LABEL[k]} error={err(`theme.${k}`)}>
                    <div className="flex items-center gap-2">
                      <input
                        type="color"
                        value={/^#[0-9a-f]{6}$/i.test(draft.theme[k]) ? draft.theme[k].toLowerCase() : "#000000"}
                        onChange={(e) => setColor(k, e.target.value)}
                        className="size-10 shrink-0"
                        aria-label={COLOR_LABEL[k]}
                      />
                      <Input
                        value={draft.theme[k]}
                        maxLength={7}
                        onChange={(e) => setColor(k, e.target.value)}
                        className="h-10 min-w-0 px-2 font-mono text-xs uppercase"
                        aria-label={`${COLOR_LABEL[k]} hex`}
                      />
                    </div>
                  </Field>
                ))}
              </div>
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
                  <Input list="tz-list" value={draft.timezone} onChange={(e) => set("timezone", e.target.value.trim())} placeholder="Europe/Bucharest" />
                  <datalist id="tz-list">
                    {TIMEZONES.map((z) => (
                      <option key={z} value={z} />
                    ))}
                  </datalist>
                </Field>
                <div className="sm:col-span-2">
                  <Toggle checked={draft.time_24h} onChange={(v) => set("time_24h", v)} label="24-hour clock" />
                </div>
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
      <div className="sticky bottom-0 z-20 -mx-4 mt-6 border-t border-border bg-bg/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
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

function BackLink() {
  return (
    <Link to="/devices" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
      <ArrowLeft className="size-4" /> Devices
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
