import { brandHtml } from "../lib/brand";
import { config, prices, formatPrice, shipCountryNames } from "../config";
import { languageCount, languageCountLabel } from "../lib/languages";
import { privacyLine } from "./features";

export interface Faq {
  q: string;
  /** HTML allowed (trusted, authored here). */
  a: string;
}

const w = prices.watch;
const c = prices.care;
const priceLine = `${formatPrice(w.GBP, "GBP")} / ${formatPrice(w.EUR, "EUR")} for the watch today, then ${formatPrice(c.GBP, "GBP")} / ${formatPrice(c.EUR, "EUR")} a month for ${brandHtml("care")} after your free ${prices.trialMonths}-month subscription. Cancel anytime.`;

export const faqGroups: { title: string; items: Faq[] }[] = [
  {
    title: "The watch",
    items: [
      {
        q: "What can ola do?",
        a: `ola is a voice companion: tap the mic button, speak, and an AI assistant answers you out loud. You can interrupt it by tapping again, talk in ${languageCountLabel} languages (or choose Auto), set reminders and take notes by voice, get live answers (weather, news, opening hours), pick or write a persona for your assistant, and switch between a blue and a white theme. It doesn't track steps or health, and it doesn't sync with a calendar.`,
      },
      {
        q: "What's the battery life?",
        a: "Battery life: we'll publish measured figures before shipping. We'd rather give you a real, measured number than an estimate.",
      },
      {
        q: "Do I need to install an app?",
        a: "No. There's no app to download and nothing that has to keep running on your phone. You set the watch up once: connect it to Wi-Fi, then link it to your account by entering a 6-digit code in any web browser. After that you're good to go: the watch works on its own and answers straight from your wrist.",
      },
      {
        q: "Does it need Wi-Fi?",
        a: "Yes. ola connects over 2.4 GHz Wi-Fi with internet access — for example your home or office network, or a phone hotspot. It has no mobile (cellular) connection. You set up Wi-Fi on the watch itself: it opens its own network called <em>ola-XXXX</em>; join it with your phone and a page opens where you pick your Wi-Fi and enter its password.",
      },
      {
        q: "What works without an internet connection?",
        a: "The clock keeps time, and notes and reminders the watch has already loaded stay on screen. Everything that needs the ola servers waits for a connection: talking to the assistant, opening a note's full text, marking a reminder done, and reminder alerts — reminders are sent to the watch by our servers when they are due, so the watch has to be online to alert you. If it was offline, reminders from the last 24 hours arrive when it reconnects.",
      },
      {
        q: "What doesn't olacompanion do?",
        a: "It has no GPS, no SIM card or mobile connection, and it can't make or take calls. It doesn't track steps, heart rate, sleep or any other health data. It doesn't sync with Google, Outlook or other calendars: reminders live in your ola account.",
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
    title: "Notes & reminders by voice",
    items: [
      {
        q: "What can I do with notes and reminders by voice?",
        a: "Create them, find them by what they say (or by a person, a place or a day), change them, copy them and delete them. Reminders can have a time, an end time, an early alert, a place and people. Notes can be edited line by line: add, change, move or remove lines. Everything you create is also in your ola account, where you can view and edit it in a browser.",
      },
      {
        q: "What if more than one note or reminder matches?",
        a: "ola asks which one you mean, and continues once you answer.",
      },
      {
        q: "Does it ask before deleting?",
        a: "Yes, in a normal conversation: ola asks you to confirm, and deletes only after a clear yes. If you are already on a note's or reminder's own screen and ask to change it, the change — including removing a line — is applied straight away, and you can undo the last change. Deleting from the watch's Delete button needs two taps.",
      },
      {
        q: "Can you give some examples?",
        a: "Example commands (not recordings): “Remind me tomorrow at 9:30 to call the dentist.” · “Add oat milk to my shopping list.” · “Move my meeting with Anna to Friday at 3.” · “What reminders do I have on Monday?” · “Copy my packing list.” · “Delete the gym reminder.” — ola then asks you to confirm.",
      },
      {
        q: "How many notes and reminders can I keep?",
        a: "Up to 100 notes and 100 reminders per account.",
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
        a: `${brandHtml("care")} pays for the AI that answers you — speech recognition, the AI model and the voice — plus the servers and updates. The assistant needs an active ${brandHtml("care")} subscription to answer; without one, the assistant doesn't answer questions; your notes and reminders stay in your account. See the <a href=\"/legal/subscription-terms/\">subscription terms</a>.`,
      },
      {
        q: "When does the free period start, and what happens after it?",
        a: `Your free ${prices.trialMonths}-month ${brandHtml("care")} subscription starts when you buy the watch. We email you a few days before the free period ends. After that the subscription renews every month and your card is charged, until you cancel.`,
      },
      {
        q: "Is there a usage limit?",
        a: `Yes. ${brandHtml("care")} includes a monthly fair-use allowance of AI usage, shared by all your watches — it is not unlimited. Different requests use different amounts (questions that search the web, long answers and long recordings use more), so it isn't a fixed number of minutes or conversations. Your account shows how much you've used as a percentage, and the date it resets; we let you know as you approach the limit. If you reach it, ola pauses new questions until the reset, or you can buy one-off extra usage for the rest of the period.`,
      },
      {
        q: `How do I cancel ${brandHtml("care")}?`,
        a: `Anytime: sign in to your account, open Account and choose “Manage billing”. If you cancel during the free ${prices.trialMonths}-month subscription you won't be charged. After cancelling, the AI assistant stops answering at the end of the period you've paid for.`,
      },
    ],
  },
  {
    title: "Delivery, returns & guarantee",
    items: [
      {
        q: "Where do you ship?",
        a: `To the United Kingdom and all 27 EU member states: ${shipCountryNames.join(", ")}. ${config.shipping.note}`,
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
        a: `We create your ola account with the email you used at checkout and send you a link to set your password, plus an order confirmation. When your watch ships, we email you tracking details. When it arrives, connect it to Wi-Fi, sign in to <a href="${config.appUrl}">your ola account</a> in any web browser, choose “Add watch” and type the 6-digit code shown on the watch. See <a href="/how-it-works/">how it works</a>.`,
      },
    ],
  },
];

export const allFaqs = faqGroups.flatMap((g) => g.items);
