// /llms.txt: a plain summary of the site for AI search engines and assistants (llmstxt.org).
import type { APIRoute } from "astro";
import { config, prices, formatPrice } from "../config";
import { languageCount } from "../lib/languages";

export const GET: APIRoute = ({ site }) => {
  const u = (p: string) => new URL(p, site).href;
  const body = `# ${config.siteName}

> olawatch is an AI smartwatch with a built-in voice assistant, sold by ${config.companyName} (${config.address}) to customers in the UK and the EU. Tap the mic on the watch, speak naturally, and the assistant answers out loud.

## What it does
- Natural voice conversation with an AI assistant; tap to talk, tap again to interrupt.
- Reminders by voice, with a time, an end time, an early alert, a place and people.
- Notes by voice: dictate, then add, change, move or delete lines.
- Live answers from the web: weather, news, opening hours, addresses, travel updates, current prices.
- ${languageCount} languages (English, the European languages and Ukrainian), plus Auto.
- Personas for the assistant; blue or white theme; conversation history you can export or delete.
- Hardware: rectangular AMOLED touch screen (410 x 502 px), microphone, speaker, Wi-Fi (2.4 GHz). No GPS, health tracking or calendar sync.

## Price
- olawatch: ${formatPrice(prices.watch.GBP, "GBP")} / ${formatPrice(prices.watch.EUR, "EUR")}, one-off, VAT included.
- olacare (the subscription that powers the assistant): ${formatPrice(prices.care.GBP, "GBP")} / ${formatPrice(prices.care.EUR, "EUR")} a month after ${prices.trialMonths} months free. Cancel anytime.
- 14-day right to cancel; UK statutory rights and the EU 2-year legal guarantee.

## Pages
- [Features](${u("/features/")}): everything the watch does
- [How it works](${u("/how-it-works/")}): from ordering to the first conversation
- [Pricing](${u("/pricing/")}): price, olacare, delivery
- [Languages](${u("/languages/")}): the full language list
- [FAQ](${u("/faq/")}): common questions
- [About](${u("/about/")}) and [Contact](${u("/contact/")}): ${config.supportEmail}
`;
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
