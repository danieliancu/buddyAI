import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FA } from "./watchIcons";
import type { DeviceLanguage, Language, Theme } from "../api";
import { WATCH_DATE_LANGS, WATCH_LANG_ALIASES, type WatchDateLang } from "../watchDates";

// Native AMOLED resolution of the Waveshare ESP32-S3 2.06" panel.
const W = 410;
const H = 502;
const BEZEL = 14;

// Watchface geometry, copied from the #defines and build_watchface() in firmware/components/ui/ui.c.
const TEXT_FONT = '"Noto Sans", Inter, ui-sans-serif, system-ui, sans-serif'; // buddy_font_20 / _28b (Noto Sans Medium / Bold)
const CLOCK_FONT = 'Montserrat, "Noto Sans", ui-sans-serif, sans-serif'; // buddy_font_clock_md, Montserrat SemiBold 76 px
const BADGE_FONT = 'Montserrat, "Noto Sans", ui-sans-serif, sans-serif'; // lv_font_montserrat_14
const STATUS_W = 300; // top status row: Wi-Fi | hint | battery, aligned TOP_MID at y 18, 30 px high
const STATUS_X = (W - STATUS_W) / 2;
const CLOCK_Y = 58; // buddy_font_clock_md: line height 56
const DATE_Y = 120; // buddy_font_20: line height 28
const DATE_MAX_W = 380; // longer dates drop the weekday
const HERO_Y = 158; // pre-rendered art (halo, wave, mic circle): full width, y 158..419
const HERO_H = 262;
const MIC_BTN_SIZE = 140;
const MIC_CX = 300;
const MIC_CY = 256;
const HALO_R = 94; // outer halo ring around the mic; the inner one is 14 px smaller
const WAVE_LOW_Y = 360; // screen y of the wave's lowest point
// The wave PNGs are 410 x 130; the firmware keeps only source rows 26..92 (ui_wave_img.c), whose
// lowest point (band row 58) sits at WAVE_LOW_Y.
const WAVE_PNG_H = 130;
const WAVE_BAND_TOP = 26;
const WAVE_BAND_H = 67;
const WAVE_BAND_LOW = 58;
const GREET_X = 24;
const GREET_Y = 196;
const GREET_W = 184;
const SHORTCUT_W = 80;
const SHORTCUT_H = 80;
const SHORTCUT_GAP = 22;
const SHORTCUT_Y = 394;
const SHORTCUT_ICON = 34; // buddy_font_shortcut: FontAwesome 34 px, line height 35, base line 5
const PREVIEW_BATTERY = 76;
const PREVIEW_COUNTS = { notes: 0, reminders: 3 };

type Rgb = [number, number, number];
const WHITE: Rgb = [255, 255, 255];
const BLACK: Rgb = [0, 0, 0];
const WAVE_BLUE: Rgb = [0x4f, 0x8c, 0xff]; // the blue theme's greeting prompt tint

/** "#rrggbb" -> [r, g, b] (0 when unparsable). */
function rgb(hex: string): Rgb {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  const n = m ? parseInt(m[1], 16) : 0;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** lv_color_mix(a, b, m): m/255 of `a`. */
function mixRgb(a: Rgb, b: Rgb, m: number): Rgb {
  return [0, 1, 2].map((i) => Math.round((a[i] * m + b[i] * (255 - m)) / 255)) as Rgb;
}

const css = (c: Rgb, opa = 1) => (opa >= 1 ? `rgb(${c.join(",")})` : `rgba(${c.join(",")},${opa})`);

/** ui_on_color(): text / icon colour readable on a fill - near-black on light fills (e.g. the "mono" theme). */
function onColor(c: Rgb): string {
  const lum = (c[0] * 54 + c[1] * 183 + c[2] * 19) >> 8; // LVGL lv_color_luminance
  return lum > 150 ? "#111318" : "#ffffff";
}

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

// STR_HELLO / STR_HELP_PROMPT per watch language, from firmware/components/ui/ui_i18n_tables.c.
const GREETINGS: Record<string, [string, string]> = {
  en: ["Hi there!", "How can I help you today?"],
  ro: ["Salut!", "Cu ce te pot ajuta azi?"],
  de: ["Hallo!", "Wie kann ich dir heute helfen?"],
  fr: ["Salut !", "Comment puis-je t’aider aujourd’hui ?"],
  es: ["¡Hola!", "¿En qué puedo ayudarte hoy?"],
  it: ["Ciao!", "Come posso aiutarti oggi?"],
  pt: ["Olá!", "Como posso ajudar-te hoje?"],
  nl: ["Hoi!", "Hoe kan ik je vandaag helpen?"],
  ca: ["Hola!", "En què et puc ajudar avui?"],
  gl: ["Ola!", "En que podo axudarte hoxe?"],
  sv: ["Hej!", "Hur kan jag hjälpa dig i dag?"],
  da: ["Hej!", "Hvordan kan jeg hjælpe dig i dag?"],
  no: ["Hei!", "Hvordan kan jeg hjelpe deg i dag?"],
  is: ["Hæ!", "Hvernig get ég aðstoðað þig í dag?"],
  fi: ["Hei!", "Miten voin auttaa sinua tänään?"],
  et: ["Tere!", "Kuidas saan sind täna aidata?"],
  lv: ["Sveiki!", "Kā es varu tev šodien palīdzēt?"],
  lt: ["Labas!", "Kuo galiu tau šiandien padėti?"],
  cy: ["Helo!", "Sut alla i dy helpu di heddiw?"],
  pl: ["Cześć!", "W czym mogę ci dziś pomóc?"],
  cs: ["Ahoj!", "S čím ti dnes můžu pomoct?"],
  sk: ["Ahoj!", "S čím ti dnes môžem pomôcť?"],
  hu: ["Szia!", "Miben segíthetek ma?"],
  sl: ["Živjo!", "Kako ti lahko danes pomagam?"],
  hr: ["Bok!", "Kako ti danas mogu pomoći?"],
  bs: ["Zdravo!", "Kako ti danas mogu pomoći?"],
  sr: ["Zdravo!", "Kako ti danas mogu pomoći?"],
  el: ["Γεια σου!", "Πώς μπορώ να σε βοηθήσω σήμερα;"],
  bg: ["Здравей!", "С какво мога да ти помогна днес?"],
  mk: ["Здраво!", "Како можам да ти помогнам денес?"],
  uk: ["Привіт!", "Чим можу допомогти сьогодні?"],
};

/** The watch's UI language for a language setting, like ui_i18n.c: the code ("de", "de-at", alias "nb"),
 * for "auto" the preferred language (the watch uses the last reply's language); unknown codes -> English. */
function uiLang(lang: DeviceLanguage, preferred: Language | null | undefined): string {
  const code = (lang === "auto" ? preferred || "en" : lang).split(/[-_]/)[0];
  const c = WATCH_LANG_ALIASES[code] ?? code;
  return WATCH_DATE_LANGS[c] && GREETINGS[c] ? c : "en";
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

/** The greeting title uses Noto Sans Bold (buddy_font_28b); index.html only loads the Medium weight. */
function useBoldFont() {
  useEffect(() => {
    const id = "watch-preview-bold-font";
    if (document.getElementById(id)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = "https://fonts.googleapis.com/css2?family=Noto+Sans:wght@700&display=swap";
    document.head.appendChild(link);
  }, []);
}

/** A circle of diameter d with a fill (flat or gradient) and a 2 px rim drawn over it, like
 * draw_circle() / fill_circle() in ui.c. */
function circle(left: number, top: number, d: number, fill: string, rim: string): CSSProperties {
  return {
    position: "absolute",
    left,
    top,
    width: d,
    height: d,
    borderRadius: "50%",
    background: fill,
    backgroundOrigin: "border-box",
    border: `2px solid ${rim}`,
    boxSizing: "border-box",
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
  useBoldFont();

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

  const lang = uiLang(language, preferredLanguage);
  const { time, date } = formatParts(now, timezone, WATCH_DATE_LANGS[lang]);
  const [hello, help] = GREETINGS[lang];
  const dim = Math.max(0, Math.min(0.85, (100 - brightness) / 100));
  const whiteTheme = theme.preset === "mono"; // theme_is_white(): its own wave and a white prompt
  const a = rgb(theme.accent), bg = rgb(theme.background);

  // hero_render(): the mic circle, light accent at the top -> accent at the bottom; mic_fg_apply()
  const micTop = mixRgb(WHITE, a, 80), micBottom = mixRgb(a, BLACK, 230);
  const micFg = onColor(mixRgb(micTop, micBottom, 128));
  // shortcuts_render(): accent towards the background, rim in a paler accent
  const scTop = mixRgb(a, bg, 95), scBottom = mixRgb(a, bg, 60);
  const scFg = onColor(mixRgb(scTop, scBottom, 128));
  const waveTop = WAVE_LOW_Y - WAVE_BAND_LOW - WAVE_BAND_TOP - HERO_Y; // the PNG's top row, in hero rows
  const innerR = HALO_R - 14;

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
              fontSize: 20,
              lineHeight: "28px",
            }}
          >
            {/* status row: Wi-Fi (accent while online) | battery */}
            <div className="absolute" style={{ left: STATUS_X, top: 19, height: 28, color: theme.accent }}>
              <Icon g={FA.wifi} size={20} />
            </div>
            <div className="absolute" style={{ right: STATUS_X, top: 19, height: 28, whiteSpace: "pre" }}>
              {`${PREVIEW_BATTERY}% `}
              <Icon g={batteryGlyph(PREVIEW_BATTERY)} size={20} />
            </div>

            {/* time: centred on the digits actually shown (like the watch); the height never changes */}
            <div
              className="absolute inset-x-0 text-center"
              style={{
                top: CLOCK_Y,
                height: 56,
                lineHeight: "56px",
                fontFamily: CLOCK_FONT,
                fontSize: 76,
                fontWeight: 600,
                color: theme.clock,
                whiteSpace: "pre",
              }}
            >
              {time}
            </div>

            {/* date: white with a hint of the accent */}
            <div
              className="absolute inset-x-0 text-center"
              style={{ top: DATE_Y, height: 28, whiteSpace: "nowrap", color: css(mixRgb(WHITE, a, 190)) }}
            >
              {date}
            </div>

            {/* hero art behind the greeting and the mic, opaque on the plain background:
                halo disc + ring, the wave, the mic circle */}
            <div
              className="absolute overflow-hidden"
              style={{ left: 0, top: HERO_Y, width: W, height: HERO_H, background: theme.background }}
            >
              <div
                style={circle(MIC_CX - HALO_R, MIC_CY - HERO_Y - HALO_R, 2 * HALO_R, css(mixRgb(a, bg, 22)), css(mixRgb(a, bg, 60)))}
              />
              <div
                style={circle(MIC_CX - innerR, MIC_CY - HERO_Y - innerR, 2 * innerR, css(mixRgb(a, bg, 38)), css(mixRgb(a, bg, 105)))}
              />
              <img
                src={whiteTheme ? "/watch-wave-white.png" : "/watch-wave.png"}
                alt=""
                draggable={false}
                style={{
                  position: "absolute",
                  left: 0,
                  top: waveTop,
                  width: W,
                  height: WAVE_PNG_H,
                  maxWidth: "none",
                  clipPath: `inset(${WAVE_BAND_TOP}px 0 ${WAVE_PNG_H - WAVE_BAND_TOP - WAVE_BAND_H}px 0)`,
                }}
              />
              <div
                style={circle(
                  MIC_CX - MIC_BTN_SIZE / 2,
                  MIC_CY - HERO_Y - MIC_BTN_SIZE / 2,
                  MIC_BTN_SIZE,
                  `linear-gradient(180deg, ${css(micTop)}, ${css(micBottom)})`,
                  css(mixRgb(WHITE, a, 110), 0.8),
                )}
              />
            </div>

            {/* greeting, left of the mic: bold white title; the prompt light blue (white in "mono") */}
            <div className="absolute flex flex-col" style={{ left: GREET_X, top: GREET_Y, width: GREET_W, gap: 4 }}>
              <div style={{ fontSize: 28, lineHeight: "39px", fontWeight: 700, color: "#ffffff", overflowWrap: "anywhere" }}>
                {hello}
              </div>
              <div
                style={{
                  lineHeight: "26px", // 28 px line, line space -2
                  paddingTop: 1,
                  color: whiteTheme ? "#ffffff" : css(mixRgb(WHITE, WAVE_BLUE, 110)),
                  overflowWrap: "anywhere",
                }}
              >
                {help}
              </div>
            </div>

            {/* mic icon (buddy_font_mic, 64 px), centred on the circle */}
            <div
              className="absolute flex items-center justify-center"
              style={{
                left: MIC_CX - MIC_BTN_SIZE / 2,
                top: MIC_CY - MIC_BTN_SIZE / 2,
                width: MIC_BTN_SIZE,
                height: MIC_BTN_SIZE,
                color: micFg,
              }}
            >
              <Icon g={FA.mic} size={64} style={{ verticalAlign: "top" }} />
            </div>

            {/* notes / reminders / settings: round buttons; a red count badge on notes / reminders */}
            <div
              className="absolute flex"
              style={{ left: (W - 3 * SHORTCUT_W - 2 * SHORTCUT_GAP) / 2, top: SHORTCUT_Y, gap: SHORTCUT_GAP }}
            >
              {[
                { g: FA.pen, count: PREVIEW_COUNTS.notes },
                { g: FA.calendar, count: PREVIEW_COUNTS.reminders },
                { g: FA.gear, count: 0 },
              ].map(({ g, count }, i) => (
                <div key={i} className="relative" style={{ width: SHORTCUT_W, height: SHORTCUT_H }}>
                  <div
                    style={circle(0, 0, SHORTCUT_W, `linear-gradient(180deg, ${css(scTop)}, ${css(scBottom)})`, css(mixRgb(WHITE, a, 60)))}
                  />
                  {/* the icon label is 35 px high, centred (top 22), its base line 5 px from the bottom */}
                  <Icon
                    g={g}
                    size={SHORTCUT_ICON}
                    style={{
                      position: "absolute",
                      left: (SHORTCUT_W - (g.w / 512) * SHORTCUT_ICON) / 2,
                      top: Math.floor((SHORTCUT_H - 35) / 2) + 30 - (448 / 512) * SHORTCUT_ICON,
                      color: scFg,
                    }}
                  />
                  {count > 0 && (
                    <div
                      className="absolute text-center"
                      style={{
                        right: 0,
                        top: 0,
                        minWidth: 20,
                        padding: "2px 5px",
                        borderRadius: 999,
                        boxSizing: "border-box",
                        background: "#F44336", // lv_palette_main(LV_PALETTE_RED)
                        color: "#ffffff",
                        fontFamily: BADGE_FONT,
                        fontSize: 14,
                        lineHeight: "16px",
                        fontWeight: 500,
                      }}
                    >
                      {count}
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* brightness */}
            <div className="pointer-events-none absolute inset-0" style={{ background: "#000", opacity: dim }} />
          </div>
        </div>
      </div>
    </div>
  );
}
