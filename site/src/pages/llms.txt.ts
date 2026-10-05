// /llms.txt: a plain summary of the site for AI search engines and assistants (llmstxt.org).
import type { APIRoute } from "astro";
import { config, prices, formatPrice, shipCountryNames } from "../config";
import { languageCount } from "../lib/languages";

export const GET: APIRoute = ({ site }) => {
  const u = (p: string) => new URL(p, site).href;
  const body = `# ${config.siteName}

> olawatch is an AI smartwatch with a built-in voice assistant, sold by ${config.companyName} (${config.address}) to customers in the UK and the EU. Tap the mic on the watch, speak naturally, and the assistant answers out loud.

## What it does
- Natural voice conversation with an AI assistant; tap to talk, tap again to interrupt.
- Reminders by voice, with a time, an end time, an early alert, a place and people.
- Notes by voice: dictate, then add, change, move or delete lines.
- Find, change, copy and delete notes and reminders by voice; if several match, it asks which one; in a conversation it asks for confirmation before deleting. Up to 100 notes and 100 reminders per account.
- Live answers from the web: weather, news, opening hours, addresses, travel updates, current prices.
- ${languageCount} languages (English, the European languages and Ukrainian), plus Auto.
- Personas for the assistant; blue or white theme; conversation history you can export or delete.
- Hardware: rectangular AMOLED touch screen (410 x 502 px), microphones, speaker, Wi-Fi (2.4 GHz). No GPS, SIM, calls, health tracking or calendar sync.

## Setup and connectivity
- No phone app: the customer web app (${config.appUrl}) runs in any browser.
- Wi-Fi setup on the watch: it opens a network called ola-XXXX; a setup page asks for the Wi-Fi network and password.
- Pairing: the watch shows a 6-digit code, entered under "Add watch" in the web app.
- Needs Wi-Fi with internet for the assistant and for reminder alerts (sent by the ola servers). Offline, the clock and already loaded notes and reminders stay visible.

## Price
- olawatch: ${formatPrice(prices.watch.GBP, "GBP")} / ${formatPrice(prices.watch.EUR, "EUR")}, one-off, VAT included.
- olacare (the subscription that powers the assistant): ${formatPrice(prices.care.GBP, "GBP")} / ${formatPrice(prices.care.EUR, "EUR")} a month after ${prices.trialMonths} months free (the free period starts at purchase). Cancel anytime from the account ("Manage billing").
- olacare includes a monthly fair-use allowance of AI usage (not unlimited), shown in the account as a percentage; at the limit the assistant pauses until the reset, or one-off extra usage can be bought.
- 14-day right to cancel; UK statutory rights and the EU 2-year legal guarantee.

## Delivery
- Ships to the United Kingdom and the 27 EU member states: ${shipCountryNames.join(", ")}.
- ${config.shipping.note}

## Pages
- [Features](${u("/features/")}): everything the watch does
- [How it works](${u("/how-it-works/")}): from ordering to the first conversation
- [Pricing](${u("/pricing/")}): price, olacare, delivery
- [Languages](${u("/languages/")}): the full language list
- [FAQ](${u("/faq/")}): common questions
- [About](${u("/about/")}) and [Contact](${u("/contact/")}): ${config.supportEmail}
- [Legal & policies](${u("/legal/")}): [terms of sale](${u("/legal/terms-of-sale/")}), [subscription terms](${u("/legal/subscription-terms/")}), [returns](${u("/legal/returns/")}), [guarantee](${u("/legal/warranty/")}), [privacy](${u("/legal/privacy/")}), [cookies](${u("/legal/cookies/")})
`;
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
};
