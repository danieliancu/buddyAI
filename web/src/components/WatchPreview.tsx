import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FA } from "./watchIcons";
import type { DeviceLanguage, Language, Theme } from "../api";
import { WATCH_DATE_LANGS, WATCH_LANG_ALIASES, type WatchDateLang } from "../watchDates";

// Native AMOLED resolution of the Waveshare ESP32-S3 2.06" panel.
const W = 410;
const H = 502;
const BEZEL = 14;

// Watchface geometry, copied from build_watchface() in firmware/components/ui/ui.c.
const TEXT_FONT = '"Noto Sans", Inter, ui-sans-serif, system-ui, sans-serif'; // buddy_font_20 / _28
const CLOCK_FONT = 'Montserrat, "Noto Sans", ui-sans-serif, sans-serif'; // buddy_font_clock, SemiBold 112 px
const STATUS_X = (W - 300) / 2; // status row: Wi-Fi left, battery right
const SHORTCUT_Y = 202;
const SHORTCUT = 70;
const SHORTCUT_GAP = 30;
const DIVIDER_GAP = 16; // between the icon row and each faded line
const MIC_SIZE = 160;
const MIC_Y = H - 34 - MIC_SIZE;
const DATE_MAX_W = 380; // longer dates drop the weekday (ui.c)
/** lv_color_mix(a, b, mix): mix/255 of `a`. */
const mix = (a: string, b: string, m: number) => `color-mix(in srgb, ${a} ${((m / 255) * 100).toFixed(1)}%, ${b})`;
const PREVIEW_BATTERY = 76;

type Glyph = { w: number; d: string };

/** A FontAwesome glyph at `size` px, sitting on the text baseline like the firmware's font glyphs. */
function Icon({ g, size, style }: { g: Glyph; size: number; style?: CSSProperties }) {
  return (
    <svg
      width={(g.w / 512) * size}
      height={size}
      viewBox={`0 -448 ${g.w} 512`}
      style={{ display: "inline-block", verticalAlign: -(64 / 512) * size, ...style }}
      aria-hidden
    >
      <path d={g.d} transform="scale(1,-1)" fill="currentColor" />
    </svg>
  );
}

function batteryGlyph(pct: number): Glyph {
  return pct >= 88 ? FA.battFull : pct >= 63 ? FA.batt3 : pct >= 38 ? FA.batt2 : pct >= 13 ? FA.batt1 : FA.battEmpty;
}

/** 1 px line, 400 px wide, fading out over 50 px at both ends (add_faded_line). */
function FadedLine({ y, color }: { y: number; color: string }) {
  return (
    <div
      className="absolute"
      style={{
        left: (W - 400) / 2,
        top: y,
        width: 400,
        height: 1,
        opacity: 0.7,
        background: `linear-gradient(90deg, transparent, ${color} 50px, ${color} 350px, transparent)`,
      }}
    />
  );
}

/** The watch's date table for a language setting, like ui_i18n.c: the code ("de", "de-at", alias "nb"),
 * for "auto" the preferred language (the watch uses the last reply's language), else English. */
function dateLang(lang: DeviceLanguage, preferred: Language | null | undefined): WatchDateLang {
  const code = (lang === "auto" ? preferred || "en" : lang).split(/[-_]/)[0];
  return WATCH_DATE_LANGS[WATCH_LANG_ALIASES[code] ?? code] ?? WATCH_DATE_LANGS.en;
}

let measureCtx: CanvasRenderingContext2D | null | undefined;

/** Width of `text` in the watch's 20 px text font (canvas measurement; 0 if unavailable). */
function textWidth(text: string): number {
  if (measureCtx === undefined) measureCtx = document.createElement("canvas").getContext("2d");
  if (!measureCtx) return 0;
  measureCtx.font = `500 20px ${TEXT_FONT}`;
  return measureCtx.measureText(text).width;
}

function fillDate(pattern: string, t: WatchDateLang, wday: number, day: number, month: number): string {
  return pattern.replace("{w}", t.wday[wday]).replace("{d}", String(day)).replace("{m}", t.mon[month]);
}

function formatParts(now: Date, tz: string, t: WatchDateLang) {
  let timeZone: string | undefined = tz;
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
  } catch {
    timeZone = undefined; // unknown zone while typing: fall back to browser zone
  }
  // Always 24-hour, like the watch.
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(now);
  const num = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const year = num("year"), month = num("month") - 1, day = num("day");
  const wday = new Date(Date.UTC(year, month, day)).getUTCDay();
  // The watch's own weekday / month names and order; without the weekday when it is too wide.
  let date = fillDate(t.date_wd, t, wday, day, month);
  if (textWidth(date) > DATE_MAX_W) date = fillDate(t.date, t, wday, day, month);
  return {
    time: `${String(num("hour")).padStart(2, "0")}:${String(num("minute")).padStart(2, "0")}`,
    date,
  };
}

export default function WatchPreview({
  theme,
  language,
  preferredLanguage,
  timezone,
  brightness,
  maxWidth = 300,
}: {
  theme: Theme;
  language: DeviceLanguage;
  preferredLanguage?: Language | null;
  timezone: string;
  brightness: number;
  maxWidth?: number;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.6);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setScale(Math.min(el.clientWidth, maxWidth) / (W + 2 * BEZEL));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [maxWidth]);

  const { time, date } = formatParts(now, timezone, dateLang(language, preferredLanguage));
  const dim = Math.max(0, Math.min(0.85, (100 - brightness) / 100));

  return (
    <div ref={box} className="w-full">
      <div className="mx-auto" style={{ width: (W + 2 * BEZEL) * scale, height: (H + 2 * BEZEL) * scale }}>
        <div
          aria-label="Watch preview"
          role="img"
          style={{
            width: W + 2 * BEZEL,
            height: H + 2 * BEZEL,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
            padding: BEZEL,
            borderRadius: 112,
            background: "linear-gradient(145deg, #3a3f4a, #15181e 60%, #2a2e36)",
            boxShadow: "0 20px 50px rgba(0,0,0,.45), inset 0 0 0 2px rgba(255,255,255,.06)",
          }}
        >
          <div
            className="relative overflow-hidden select-none"
            style={{
              width: W,
              height: H,
              borderRadius: 98,
              background: theme.background,
              color: theme.text,
              fontFamily: TEXT_FONT,
              fontWeight: 500,
            }}
          >
            {/* status row: Wi-Fi (accent while online) | battery */}
            <div className="absolute" style={{ left: STATUS_X, top: 19, height: 28, lineHeight: "28px", fontSize: 20, color: theme.accent }}>
              <Icon g={FA.wifi} size={20} />
            </div>
            <div className="absolute" style={{ right: STATUS_X, top: 19, height: 28, lineHeight: "28px", fontSize: 20, whiteSpace: "pre" }}>
              {`${PREVIEW_BATTERY}% `}
              <Icon g={batteryGlyph(PREVIEW_BATTERY)} size={20} />
            </div>

            {/* time: centred on the digits actually shown (like the watch); the height never changes */}
            <div
              className="absolute inset-x-0 flex justify-center"
              style={{
                top: 58,
                height: 82,
                lineHeight: "82px",
                fontFamily: CLOCK_FONT,
                fontSize: 112,
                fontWeight: 600,
                color: theme.clock,
                whiteSpace: "pre",
              }}
            >
              {time}
            </div>

            {/* date */}
            <div className="absolute inset-x-0 text-center" style={{ top: 152, lineHeight: "28px", fontSize: 20, whiteSpace: "nowrap" }}>
              {date}
            </div>

            {/* notes / reminders / settings: gradient circles with a light rim */}
            <FadedLine y={SHORTCUT_Y - DIVIDER_GAP} color={theme.accent} />
            <div className="absolute flex" style={{ left: (W - 3 * SHORTCUT - 2 * SHORTCUT_GAP) / 2, top: SHORTCUT_Y, gap: SHORTCUT_GAP }}>
              {[FA.pen, FA.calendar, FA.gear].map((g, i) => (
                <div
                  key={i}
                  className="flex justify-center rounded-full"
                  style={{
                    width: SHORTCUT,
                    height: SHORTCUT,
                    paddingTop: 20.5,
                    color: "#fff",
                    background: `linear-gradient(180deg, ${mix(theme.accent, theme.background, 130)}, ${mix(theme.accent, theme.background, 45)})`,
                    border: `2px solid ${mix(mix("#ffffff", theme.accent, 90), "transparent", 153)}`,
                    boxSizing: "border-box",
                  }}
                >
                  <Icon g={g} size={28} style={{ verticalAlign: "top" }} />
                </div>
              ))}
            </div>
            <FadedLine y={SHORTCUT_Y + SHORTCUT + DIVIDER_GAP} color={theme.accent} />

            {/* mic button */}
            <div
              className="absolute flex items-center justify-center rounded-full"
              style={{
                left: (W - MIC_SIZE) / 2,
                top: MIC_Y,
                width: MIC_SIZE,
                height: MIC_SIZE,
                color: "#fff",
                background: `linear-gradient(180deg, ${mix("#ffffff", theme.accent, 110)}, ${mix(theme.accent, "#000000", 215)})`,
                border: `2px solid ${mix(mix("#ffffff", theme.accent, 140), "transparent", 204)}`,
                boxSizing: "border-box",
              }}
            >
              <Icon g={FA.mic} size={80} style={{ verticalAlign: "top" }} />
            </div>

            {/* brightness */}
            <div className="pointer-events-none absolute inset-0" style={{ background: "#000", opacity: dim }} />
          </div>
        </div>
      </div>
    </div>
  );
}
