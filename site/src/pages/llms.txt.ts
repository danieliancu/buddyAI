// /llms.txt: a plain summary of the site for AI search engines and assistants (llmstxt.org).
import type { APIRoute } from "astro";
import { config, prices, formatPrice, shipCountryNames } from "../config";
import { languageCount } from "../lib/languages";

export const GET: APIRoute = ({ site }) => {
  const u = (p: string) => new URL(p, site).href;
  const body = `# ${config.siteName}

> olacompanion is an AI smartwatch with a built-in voice assistant, sold by ${config.companyName} (${config.address}) to customers in the UK and the EU. Tap the mic on the watch, speak naturally, and the assistant answers out loud.

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
- Wi-Fi setup from the ola account: on Android, Chrome sends the Wi-Fi network and password to the watch over Bluetooth (encrypted, unlocked with a setup password shown on the watch); on iPhone, the watch opens a network called ola-XXXX (protected by the password shown on the watch, with a QR code) and a setup page asks for the Wi-Fi network and password. The watch uses 2.4 GHz Wi-Fi.
- Pairing: the watch shows a 6-digit code, entered in the web app. Pairing starts the free olacare trial.
- Needs Wi-Fi with internet for the assistant and for reminder alerts (sent by the ola servers). Offline, the clock and already loaded notes and reminders stay visible.

## Price
- olacompanion: ${formatPrice(prices.watch.GBP, "GBP")} / ${formatPrice(prices.watch.EUR, "EUR")}, one-off, VAT included.
- olacare (the subscription that powers the assistant): ${formatPrice(prices.care.GBP, "GBP")} / ${formatPrice(prices.care.EUR, "EUR")} a month after a free ${prices.trialMonths}-month trial, which starts when the watch is paired (the card is saved at checkout; nothing is charged for olacare before the trial ends). Cancel anytime from the account ("Manage billing").
- ${prices.interactionsPerMonth.toLocaleString("en-GB")} AI interactions per month included with olacare, shared by the account's watches (one request = one interaction; web searches and other tools used for it do not count extra; unused interactions do not carry over). When they are used up the assistant answers again at renewal, or ${prices.topup.interactions} more can be bought for the rest of the month (${formatPrice(prices.topup.GBP, "GBP")}, one-off).
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
