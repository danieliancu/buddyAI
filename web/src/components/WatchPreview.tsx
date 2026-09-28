import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { BatteryMedium, Mic, Wifi } from "lucide-react";
import type { Language, Theme } from "../api";

// Native AMOLED resolution of the Waveshare ESP32-S3 2.06" panel.
const W = 410;
const H = 502;
const BEZEL = 14;

function formatParts(now: Date, tz: string, lang: Language, h24: boolean) {
  const locale = lang === "ro" ? "ro-RO" : "en-GB";
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
  // "Monday, 28 September" / "Luni, 28 septembrie" (assembled from parts: separators differ per ICU version).
  const dp = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", timeZone }).formatToParts(now);
  const part = (t: string) => dp.find((p) => p.type === t)?.value ?? "";
  const date = `${part("weekday")}, ${part("day")} ${part("month")}`;
  return {
    time: `${h24 ? hour.padStart(2, "0") : hour}:${minute}`,
    period: h24 ? "" : period,
    date: date.charAt(0).toUpperCase() + date.slice(1),
  };
}

export default function WatchPreview({
  theme,
  language,
  time24h,
  timezone,
  brightness,
  maxWidth = 300,
}: {
  theme: Theme;
  language: Language;
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

  const { time, period, date } = formatParts(now, timezone, language, time24h);
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
              fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
            }}
          >
            {/* status bar */}
            <div className="absolute inset-x-0 top-7 flex items-center justify-center gap-3" style={{ color: theme.text }}>
              <Wifi size={26} strokeWidth={2.2} />
              <span className="flex items-center gap-1" style={{ fontSize: 22, fontWeight: 600 }}>
                <BatteryMedium size={30} strokeWidth={2} />
                76%
              </span>
            </div>

            {/* clock */}
            <div className="absolute inset-x-0 flex flex-col items-center" style={{ top: 72 }}>
              <div className="flex items-start" style={{ color: theme.clock }}>
                <span className="tabular" style={{ fontSize: 132, fontWeight: 700, lineHeight: 1, letterSpacing: -4 }}>
                  {time}
                </span>
                {period && <span style={{ fontSize: 30, fontWeight: 600, marginTop: 14, marginLeft: 6 }}>{period}</span>}
              </div>
              <div style={{ fontSize: 30, marginTop: 14, fontWeight: 500 }}>{date}</div>
            </div>

            {/* mic button */}
            <div className="absolute inset-x-0 flex flex-col items-center" style={{ top: 268 }}>
              <div
                className="grid place-items-center rounded-full"
                style={{
                  width: 150,
                  height: 150,
                  background: theme.accent,
                  color: theme.background,
                  boxShadow: `0 0 0 14px ${theme.accent}26, 0 0 60px ${theme.accent}55`,
                }}
              >
                <Mic size={68} strokeWidth={2.2} />
              </div>
              <div style={{ fontSize: 22, marginTop: 22, opacity: 0.85 }}>
                {language === "ro" ? "Atinge pentru a vorbi" : "Tap to talk"}
              </div>
            </div>

            {/* brightness */}
            <div className="pointer-events-none absolute inset-0" style={{ background: "#000", opacity: dim }} />
          </div>
        </div>
      </div>
    </div>
  );
}
