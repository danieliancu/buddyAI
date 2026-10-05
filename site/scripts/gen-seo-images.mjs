// Generates the static SEO images in public/: the Open Graph / social share image (1200 x 630) and the
// app icons (favicon PNGs, apple-touch-icon). Run after changing the brand artwork:
//   node scripts/gen-seo-images.mjs
import sharp from "sharp";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const pub = (f) => root + "public/" + f;
const asset = (f) => root + "src/assets/home/" + f;

// The watch price shown on the share image comes from src/config.ts (PRICE_GBP), so it can't drift from the site.
const watchGbp = Number(/PRICE_GBP\s*=\s*\{\s*watch:\s*([\d.]+)/.exec(readFileSync(root + "src/config.ts", "utf8"))?.[1]);
if (!watchGbp) throw new Error("PRICE_GBP.watch not found in src/config.ts");
const priceLabel = Number.isInteger(watchGbp) ? `£${watchGbp}` : `£${watchGbp.toFixed(2)}`;

// --- icons: the waveform mark on a white rounded square ---------------------------------------
const mark = (size) => `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 32 32">
  <defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#5b4cf0"/><stop offset="1" stop-color="#2f6bff"/></linearGradient></defs>
  <rect width="32" height="32" rx="7" fill="#ffffff"/>
  <g stroke="url(#g)" stroke-width="2.6" stroke-linecap="round"><path d="M6 14v4M11 10.5v11M16 7v18M21 10.5v11M26 14v4"/></g>
</svg>`;
for (const [name, size] of [["icon-192.png", 192], ["icon-512.png", 512], ["apple-touch-icon.png", 180]]) {
  await sharp(Buffer.from(mark(size))).png().toFile(pub(name));
}

// --- Open Graph image: mountains background, headline, the watch -----------------------------
const W = 1200, H = 630;
const bg = await sharp(asset("hero-bg.png")).resize(W, H, { fit: "cover" }).toBuffer();
const watch = await sharp(asset("hero-watch.png")).resize({ height: 600 }).toBuffer();
const text = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}">
  <defs>
    <linearGradient id="fade" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity=".92"/><stop offset=".55" stop-color="#fff" stop-opacity=".6"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
    <linearGradient id="hl" x1="0" x2="1"><stop offset="0" stop-color="#2f6bff"/><stop offset="1" stop-color="#8b3dff"/></linearGradient>
    <linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#5b4cf0"/><stop offset="1" stop-color="#2f6bff"/></linearGradient>
  </defs>
  <rect width="${W}" height="${H}" fill="url(#fade)"/>
  <g transform="translate(80 120)" stroke="url(#g)" stroke-width="5" stroke-linecap="round">
    <path d="M0 12v8M10 5v22M20 -2v36M30 5v22M40 12v8"/>
  </g>
  <text x="138" y="146" font-family="Segoe UI, Arial, sans-serif" font-size="40" font-weight="700" fill="#0f1533">ola<tspan fill="#5b4cf0">watch</tspan></text>
  <text x="80" y="268" font-family="Segoe UI, Arial, sans-serif" font-size="64" font-weight="800" fill="#0f1533">Your AI companion,</text>
  <text x="80" y="346" font-family="Segoe UI, Arial, sans-serif" font-size="64" font-weight="800" fill="url(#hl)">now on your wrist.</text>
  <text x="80" y="420" font-family="Segoe UI, Arial, sans-serif" font-size="30" fill="#39415a">Talk, set reminders, take notes, get live answers.</text>
  <text x="80" y="470" font-family="Segoe UI, Arial, sans-serif" font-size="30" font-weight="700" fill="#0f1533">${priceLabel} · ships to the UK &amp; EU</text>
</svg>`;
await sharp(bg)
  .composite([
    { input: Buffer.from(text), top: 0, left: 0 },
    { input: watch, top: 15, left: 640 },
  ])
  .png({ compressionLevel: 9 })
  .toFile(pub("og-image.png"));

console.log("SEO images written to public/");
