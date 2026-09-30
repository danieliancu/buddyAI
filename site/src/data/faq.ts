import { config, prices, formatPrice, deliveryText } from "../config";
import { languageCount, languageCountLabel } from "../lib/languages";
import { privacyLine } from "./features";

export interface Faq {
  q: string;
  /** HTML allowed (trusted, authored here). */
  a: string;
}

const w = prices.watch;
const c = prices.care;
const priceLine = `${formatPrice(w.GBP, "GBP")} / ${formatPrice(w.EUR, "EUR")} for the watch today, then ${formatPrice(c.GBP, "GBP")} / ${formatPrice(c.EUR, "EUR")} a month for ola Care after ${prices.trialMonths} months free. Cancel anytime.`;

export const faqGroups: { title: string; items: Faq[] }[] = [
  {
    title: "The watch",
    items: [
      {
        q: "What can ola do?",
        a: `ola is a voice companion: tap the mic button, speak, and an AI assistant answers you out loud. You can interrupt it by tapping again, talk in ${languageCountLabel} languages (or choose Auto), pick or write a persona for your assistant, and choose your own screen colours. It is a conversation partner — it doesn't track steps or health, and it doesn't set reminders or manage a calendar.`,
      },
      {
        q: "What's the battery life?",
        a: "Battery life: we'll publish measured figures before shipping. We'd rather give you a real, measured number than an estimate.",
      },
      {
        q: "Does it need Wi-Fi?",
        a: "Yes. ola connects over Wi-Fi (2.4 GHz) — for example your home or office network, or a phone hotspot. It has no mobile (cellular) connection, so it can't answer without Wi-Fi.",
      },
      {
        q: "What does the screen look like?",
        a: "It's a rectangular AMOLED touch display, 410 × 502 pixels. The watch shown on this site is an illustration — real product photos are coming soon.",
      },
      {
        q: "How does the watch get new features?",
        a: "Firmware updates are delivered over the air via Wi-Fi.",
      },
      {
        q: "Can the AI get things wrong?",
        a: "Yes. Like all AI assistants, ola can make mistakes. Please double-check anything important, and don't rely on it for medical, legal, financial or emergency advice.",
      },
    ],
  },
  {
    title: "Languages",
    items: [
      {
        q: "Which languages does ola speak?",
        a: `${languageCount} languages today, plus Auto, which answers in the language you speak. <a href="/languages/">See the full list</a>. Speech recognition quality varies between languages.`,
      },
    ],
  },
  {
    title: "Privacy",
    items: [
      {
        q: "What happens to my voice?",
        a: `${privacyLine} Read the <a href="/legal/privacy/">privacy notice</a> for details.`,
      },
      {
        q: "Can I see or delete my conversation history?",
        a: "Yes. Your history is in your ola account. You can export it or delete it anytime.",
      },
      {
        q: "Do you use cookies or tracking?",
        a: `This website uses no tracking cookies. ${config.plausibleDomain ? "We use cookieless, privacy-friendly analytics (Plausible) to count visits." : "If we add analytics, it will be cookieless."} See the <a href="/legal/cookies/">cookie notice</a>.`,
      },
    ],
  },
  {
    title: "Price & subscription",
    items: [
      {
        q: "How much does it cost?",
        a: `${priceLine} Prices include VAT.`,
      },
      {
        q: "Why is there a subscription?",
        a: "ola Care pays for the AI that answers you — speech recognition, the AI model and the voice — plus the servers and updates. The assistant needs an active ola Care subscription to work. It includes a monthly fair-use allowance, described in the <a href=\"/legal/subscription-terms/\">subscription terms</a>.",
      },
      {
        q: "How do I cancel ola Care?",
        a: `Anytime, from your account (Plan &amp; usage). If you cancel during the ${prices.trialMonths}-month free period you won't be charged. After cancelling, the AI assistant stops answering at the end of the period you've paid for.`,
      },
    ],
  },
  {
    title: "Delivery, returns & guarantee",
    items: [
      {
        q: "Where do you ship?",
        a: `To the United Kingdom and the European Union: ${config.shipping.countries.join(", ")}. Delivery times: UK ${deliveryText(config.shipping.deliveryTimes.UK)}, EU ${deliveryText(config.shipping.deliveryTimes.EU)}. ${config.shipping.costNote}`,
      },
      {
        q: "Can I return it?",
        a: `Yes. You have a 14-day right to cancel after delivery under UK and EU distance-selling rules — you don't need to give a reason. ${config.freeReturns ? "Returns are free." : "Unless the watch is faulty, you pay the cost of sending it back."} See <a href="/legal/returns/">returns &amp; cancellation</a>.`,
      },
      {
        q: "What guarantee do I get?",
        a: "In the UK you have your statutory rights under the Consumer Rights Act 2015. In the EU you have a 2-year legal guarantee of conformity. These rights are not affected by anything we say. See <a href=\"/legal/warranty/\">guarantee &amp; warranty</a>.",
      },
      {
        q: "What happens after I order?",
        a: `You'll get an order confirmation email with a link to set your account password. When your watch ships, we email you tracking details. When it arrives, sign in at <a href="${config.appUrl}">the ola app</a> and choose Add watch.`,
      },
    ],
  },
];

export const allFaqs = faqGroups.flatMap((g) => g.items);
