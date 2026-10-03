import { languageCount, languageCountLabel } from "../lib/languages";

export type IconName =
  | "mic" | "tap" | "globe" | "persona" | "palette" | "history" | "display" | "speaker" | "wifi" | "update"
  | "bell" | "note" | "search" | "lock";

export interface Feature {
  icon: IconName;
  title: string;
  short: string;
  long: string;
}

// Only features that exist in the product today (truthful-content rules).
export const features: Feature[] = [
  {
    icon: "mic",
    title: "Natural voice conversation",
    short: "Talk to an AI assistant, and hear it answer out loud.",
    long: "Ask questions, think out loud, or just chat. ola listens and answers in a natural voice through the watch speaker — a conversation, not a list of commands.",
  },
  {
    icon: "tap",
    title: "Tap to talk, tap to interrupt",
    short: "One big mic button. Tap again to cut in.",
    long: "Tap the big mic button on the screen and start speaking. If you've heard enough or want to change direction, tap again to interrupt the reply.",
  },
  {
    icon: "bell",
    title: "Reminders",
    short: "Set reminders by voice, with a time, place and people.",
    long: "Say \"remind me tomorrow at 9:30 to call the dentist\". Add an end time, an early alert, a place or the people involved. When it's due, the watch shows the reminder and plays a short alert tone.",
  },
  {
    icon: "note",
    title: "Notes",
    short: "Create and edit notes just by talking.",
    long: "Dictate a note, then add, change, move or delete lines by voice. Your notes and reminders are also in your ola account, where you can view and edit them.",
  },
  {
    icon: "search",
    title: "Live answers",
    short: "Weather, news, opening hours and more.",
    long: "For things that change, ola looks them up: today's weather, the news, opening hours, addresses, travel updates and current prices. You can turn this off for each watch.",
  },
  {
    icon: "globe",
    title: `${languageCountLabel} languages`,
    short: "Or choose Auto: it answers in the language you speak.",
    long: `Pick one of ${languageCountLabel} languages, or choose Auto and ola replies in whichever language you speak to it.`,
  },
  {
    icon: "persona",
    title: "Personas",
    short: "Choose — or write — your assistant's personality.",
    long: "Pick a ready-made persona, or write your own: a patient explainer, a concise helper, a playful friend. Your assistant, your style.",
  },
  {
    icon: "palette",
    title: "Blue or white",
    short: "Two themes for the screen: blue or white.",
    long: "Switch the watch between its blue and white themes, on the watch, in the ola app or just by asking.",
  },
  {
    icon: "history",
    title: "History you control",
    short: "Your conversations in your account. Export or delete anytime.",
    long: "Your conversation history lives in your ola account. You can export it or delete it at any time.",
  },
];

export const hardware = [
  { icon: "display" as IconName, title: "AMOLED touch display", text: "A 410 × 502 pixel rectangular AMOLED touch screen: deep blacks, crisp text." },
  { icon: "mic" as IconName, title: "Built-in microphones", text: "Tap the mic and speak naturally." },
  { icon: "speaker" as IconName, title: "Built-in speaker", text: "Replies are spoken out loud, straight from the watch." },
  { icon: "wifi" as IconName, title: "Wi-Fi + over-the-air updates", text: "Connects over Wi-Fi and gets firmware updates over the air." },
];

export const privacyLine =
  "Your voice is processed securely by our AI partners (OpenAI) to answer you. We never sell your data, and you can delete your history anytime.";

// The six cards under the home page hero.
export const highlights: { icon: IconName; tint?: "blue" | "green" | "teal"; title: string; text: string }[] = [
  { icon: "mic", title: "Voice Assistant", text: "Natural conversations, just tap and talk." },
  { icon: "bell", tint: "blue", title: "Smart Reminders", text: "Time, place, people and early alerts." },
  { icon: "note", tint: "green", title: "Notes by Voice", text: "Create and edit notes without typing." },
  { icon: "search", tint: "blue", title: "Live Answers", text: "Weather, news, opening hours and more." },
  { icon: "lock", tint: "blue", title: "Private & Secure", text: "See, export or delete your history anytime." },
  { icon: "globe", tint: "teal", title: `${languageCount} Languages`, text: "Or Auto: it answers in the language you speak." },
];
