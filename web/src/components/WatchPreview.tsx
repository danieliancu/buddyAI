import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FA } from "./watchIcons";
import type { DeviceLanguage, Language, Theme } from "../api";

// Native AMOLED resolution of the Waveshare ESP32-S3 2.06" panel.
const W = 410;
const H = 502;
const BEZEL = 14;

// Watchface geometry, copied from build_watchface() in firmware/components/ui/ui.c.
const TEXT_FONT = '"Noto Sans", Inter, ui-sans-serif, system-ui, sans-serif'; // buddy_font_20 / _28
const CLOCK_FONT = 'Montserrat, "Noto Sans", ui-sans-serif, sans-serif'; // buddy_font_clock, SemiBold 112 px
const DIGIT_W = 76; // widest clock digit; hours and minutes each sit in a box two digits wide
const COLON_W = 27;
const STATUS_X = (W - 300) / 2; // status row: Wi-Fi left, battery right
const SHORTCUT_Y = (185 + 308) / 2 - 62 / 2;
const MIC_SIZE = 150;
const MIC_Y = H - 44 - MIC_SIZE;
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

/** 1 px line fading out over 60 px at both ends (add_faded_line). */
function FadedLine({ y, color }: { y: number; color: string }) {
  return (
    <div
      className="absolute"
      style={{
        left: STATUS_X,
        top: y,
        width: 300,
        height: 1,
        opacity: 0.8,
        background: `linear-gradient(90deg, transparent, ${color} 60px, ${color} 240px, transparent)`,
      }}
    />
  );
}

/** Locale for the preview date: the device language; for "auto" the preferred language, else English. */
function dateLocale(lang: DeviceLanguage, preferred: Language | null | undefined): string {
  const code = lang === "auto" ? preferred || "en" : lang;
  if (code === "en") return "en-GB";
  try {
    return Intl.DateTimeFormat.supportedLocalesOf([code]).length ? code : "en-GB";
  } catch {
    return "en-GB"; // malformed tag
  }
}

function formatParts(now: Date, tz: string, locale: string, h24: boolean) {
  let timeZone: string | undefined = tz;
  try {
    new Intl.DateTimeFormat("en", { timeZone: tz });
  } catch {
    timeZone = undefined; // unknown zone while typing: fall back to browser zone
  }
  const tp = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: !h24, hourCycle: h24 ? "h23" : "h12", timeZone }).formatToParts(now);
  const hour = tp.find((p) => p.type === "hour")?.value ?? "0";
  const minute = tp.find((p) => p.type === "minute")?.value ?? "00";
  const period = tp.find((p) => p.type === "dayPeriod")?.value ?? "";
  // "Monday, 28 September" / "Montag, 28. September".
  const dp = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", timeZone }).formatToParts(now);
  const part = (t: string) => dp.find((p) => p.type === t)?.value ?? "";
  // en-GB is assembled from parts (its separators differ per ICU version); other locales keep their native order.
  const date =
    locale === "en-GB"
      ? `${part("weekday")}, ${part("day")} ${part("month")}`
      : dp.map((p) => p.value).join("");
  return {
    time: `${h24 ? hour.padStart(2, "0") : hour}:${minute}`,
    period: h24 ? "" : period,
    date: date.charAt(0).toUpperCase() + date.slice(1),
  };
}

export default function WatchPreview({
  theme,
  language,
  preferredLanguage,
  time24h,
  timezone,
  brightness,
  maxWidth = 300,
}: {
  theme: Theme;
  language: DeviceLanguage;
  preferredLanguage?: Language | null;
  time24h: boolean;
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

  const { time, date } = formatParts(now, timezone, dateLocale(language, preferredLanguage), time24h);
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

            {/* time: hours right-aligned against the colon, minutes left-aligned */}
            <div
              className="absolute flex"
              style={{
                left: (W - 4 * DIGIT_W - COLON_W) / 2,
                top: 58,
                height: 82,
                lineHeight: "82px",
                fontFamily: CLOCK_FONT,
                fontSize: 112,
                fontWeight: 600,
                color: theme.clock,
              }}
            >
              <span style={{ width: 2 * DIGIT_W, textAlign: "right" }}>{time.split(":")[0]}</span>
              <span style={{ width: COLON_W, textAlign: "center" }}>:</span>
              <span style={{ width: 2 * DIGIT_W, textAlign: "left" }}>{time.split(":")[1]}</span>
            </div>

            {/* date */}
            <div className="absolute inset-x-0 text-center" style={{ top: 146, lineHeight: "40px", fontSize: 28, whiteSpace: "nowrap" }}>
              {date}
            </div>

            {/* notes / reminders / settings shortcuts between two faded lines */}
            <FadedLine y={SHORTCUT_Y - 10} color={theme.text} />
            <div className="absolute flex" style={{ left: (W - 3 * 62 - 2 * 22) / 2, top: SHORTCUT_Y, gap: 22 }}>
              {[FA.pen, FA.calendar, FA.gear].map((g, i) => (
                <div key={i} className="flex justify-center" style={{ width: 62, height: 62, paddingTop: 13.75, opacity: 0.8 }}>
                  <Icon g={g} size={34} style={{ verticalAlign: "top" }} />
                </div>
              ))}
            </div>
            <FadedLine y={SHORTCUT_Y + 62 + 10} color={theme.text} />

            {/* mic button */}
            <div
              className="absolute flex items-center justify-center rounded-full"
              style={{ left: (W - MIC_SIZE) / 2, top: MIC_Y, width: MIC_SIZE, height: MIC_SIZE, background: theme.accent, color: "#fff" }}
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
