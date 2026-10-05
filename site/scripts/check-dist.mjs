// Checks the built site (dist/) for rules that can regress: no placeholders or demo domains in public pages,
// indexing (robots, noindex, sitemap), internal links, canonicals, the app URL and the structured data.
// Run after a build:  npm run check   (= astro build && node scripts/check-dist.mjs)
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const SITE = "https://www.olawatch.ai";
const APP = "https://app.olawatch.ai";
// The UK and the 27 EU member states.
const COUNTRIES = "GB AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split(" ");

const errors = [];
const fail = (file, msg) => errors.push(`${file}: ${msg}`);

if (!existsSync(dist)) {
  console.error("dist/ not found: run `astro build` first.");
  process.exit(1);
}

const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
const files = walk(dist);
const rel = (p) => relative(dist, p).split(sep).join("/");
const pages = files.filter((f) => f.endsWith(".html"));
const texts = files.filter((f) => /\.(html|txt|xml|webmanifest)$/.test(f));

// --- placeholders and demo domains ---------------------------------------------------------------------
const banned = [/\bDRAFT\b/, /needs legal review/i, /\bTODO\b/, /\bTBD\b/, /to be confirmed/i, /example\.(com|org)/i, /app\.example/i];
for (const f of texts) {
  const s = readFileSync(f, "utf8");
  for (const re of banned) if (re.test(s)) fail(rel(f), `contains ${re}`);
}

// --- internal links, canonicals, app URL ----------------------------------------------------------------
const exists = (path) => {
  const clean = decodeURIComponent(path.split(/[?#]/)[0]);
  if (clean === "" || clean === "/") return existsSync(join(dist, "index.html"));
  const p = join(dist, clean);
  return existsSync(p) && statSync(p).isFile() ? true : existsSync(join(p, "index.html"));
};
for (const f of pages) {
  const s = readFileSync(f, "utf8");
  const name = rel(f);
  for (const [, href] of s.matchAll(/href="([^"]*)"/g)) {
    if (href.startsWith("/") && !href.startsWith("//") && !exists(href)) fail(name, `broken internal link ${href}`);
    if (/^https?:\/\/app\./.test(href) && !href.startsWith(APP)) fail(name, `app link not on ${APP}: ${href}`);
  }
  const canonical = /<link rel="canonical" href="([^"]+)"/.exec(s)?.[1];
  if (!canonical) fail(name, "no canonical");
  else if (!canonical.startsWith(SITE + "/") || !canonical.endsWith("/")) fail(name, `bad canonical ${canonical}`);
}

// --- indexing --------------------------------------------------------------------------------------------
const read = (p) => (existsSync(join(dist, p)) ? readFileSync(join(dist, p), "utf8") : (fail(p, "missing"), ""));
const robotsMeta = (p) => /<meta name="robots" content="([^"]+)"/.exec(read(p))?.[1] ?? "";
if (!robotsMeta("thank-you/index.html").includes("noindex")) fail("thank-you/index.html", "must be noindex");
if (!robotsMeta("404.html").includes("noindex")) fail("404.html", "must be noindex");
for (const p of pages.map(rel).filter((p) => !["thank-you/index.html", "404.html"].includes(p))) {
  if (robotsMeta(p).includes("noindex")) fail(p, "unexpected noindex");
}
const robots = read("robots.txt");
if (/^Disallow:\s*\/\s*$/m.test(robots)) fail("robots.txt", "disallows the whole site");
if (/^Disallow:.*thank-you/m.test(robots)) fail("robots.txt", "blocks /thank-you/ (crawlers must see its noindex)");
if (!robots.includes(`Sitemap: ${SITE}/sitemap-index.xml`)) fail("robots.txt", "no sitemap line");
if (!existsSync(join(dist, "legal/index.html"))) fail("legal/index.html", "missing /legal/ page");

const sitemap = files.filter((f) => /sitemap-\d+\.xml$/.test(f)).map((f) => readFileSync(f, "utf8")).join("\n");
const urls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
if (!urls.length) fail("sitemap", "no URLs");
if (/<lastmod>/.test(sitemap)) fail("sitemap", "contains lastmod (the build date is not a content change)");
for (const u of urls) {
  if (!u.startsWith(SITE + "/") || !u.endsWith("/")) fail("sitemap", `bad URL ${u}`);
  if (/thank-you|404/.test(u)) fail("sitemap", `lists a noindex page: ${u}`);
}
for (const must of ["/", "/legal/", "/pricing/", "/faq/", "/legal/privacy/"]) {
  if (!urls.includes(SITE + must)) fail("sitemap", `missing ${must}`);
}
const indexable = pages.map(rel).filter((p) => !["thank-you/index.html", "404.html"].includes(p));
for (const p of indexable) {
  const url = SITE + "/" + p.replace(/index\.html$/, "");
  if (!urls.includes(url)) fail("sitemap", `indexable page not listed: ${url}`);
}

// --- structured data -------------------------------------------------------------------------------------
const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
for (const f of pages) {
  const s = readFileSync(f, "utf8");
  for (const [, json] of s.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)) {
    let data;
    try {
      data = JSON.parse(json);
    } catch (e) {
      fail(rel(f), `invalid JSON-LD: ${e.message}`);
      continue;
    }
    for (const item of Array.isArray(data) ? data : [data]) {
      if (["Review", "AggregateRating"].includes(item["@type"]) || item.review || item.aggregateRating) {
        fail(rel(f), "review markup is not allowed (testimonials are not verified reviews)");
      }
      if (item["@type"] === "Organization") {
        const area = item.contactPoint?.areaServed ?? [];
        if (!sameSet(area, COUNTRIES)) fail(rel(f), "Organization areaServed is not the 28 countries");
      }
      if (item["@type"] !== "Product") continue;
      for (const offer of item.offers ?? []) {
        const countries = offer.hasMerchantReturnPolicy?.applicableCountry ?? [];
        if (new Set(countries).size !== countries.length) fail(rel(f), "duplicate return-policy countries");
        if (!sameSet(countries, COUNTRIES)) fail(rel(f), `return-policy countries differ from the 28: ${countries}`);
        if (offer.availability) fail(rel(f), "static availability (it must follow the live shop status)");
        if (!(Number(offer.price) > 0) || !["GBP", "EUR"].includes(offer.priceCurrency)) fail(rel(f), "bad offer price");
        const visible = offer.priceCurrency === "GBP" ? `£${Number(offer.price).toFixed(2)}` : `€${Number(offer.price).toFixed(2)}`;
        if (!s.includes(visible)) fail(rel(f), `offer price ${visible} is not shown on the page`);
      }
    }
  }
}

if (errors.length) {
  console.error(`check-dist: ${errors.length} problem(s)\n` + errors.map((e) => "  - " + e).join("\n"));
  process.exit(1);
}
console.log(`check-dist: OK (${pages.length} pages, ${urls.length} sitemap URLs)`);
