import { languageCount, languageCountLabel } from "../lib/languages";

export type IconName =
  | "mic" | "tap" | "globe" | "persona" | "palette" | "history" | "display" | "speaker" | "wifi" | "update"
  | "bell" | "note" | "search" | "lock" | "mail" | "tapRays" | "chat" | "calendar" | "calendarCheck" | "mailSend" | "clock";

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
    long: "Ask questions, think out loud, or just chat. olá listens and answers in a natural voice through the watch speaker — a conversation, not a list of commands.",
  },
  {
    icon: "tap",
    title: "Tap to talk, tap to interrupt",
    short: "One big mic button. Tap again to cut in.",
    long: "Tap the big mic button on the screen and start speaking. If you've heard enough or want to change direction, tap again to interrupt the reply.",
  },
  {
    icon: "chat",
    title: "Dialog mode",
    short: "Hold the mic for 2 seconds and keep talking, no tapping.",
    long: "Hold the mic button for 2 seconds: the watch says \"Dialog mode on\" in its own language and opens the mic again after every reply, so you can go back and forth without tapping. It ends by itself when you stop talking, or with a tap.",
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
    long: "Dictate a note, then add, change, move or delete lines by voice. Send any note to your email, by voice or with a tap on the watch. Find, copy or delete notes and reminders just by asking; if several match, olá asks which one. Your notes and reminders are also in your olá account, where you can view and edit them.",
  },
  {
    icon: "calendar",
    title: "Google Calendar & Outlook",
    short: "Hear what's next and add events to your calendar by voice.",
    long: "Connect Google Calendar or Microsoft Outlook in your olá account, then just ask: \"What's on tomorrow?\", \"Am I free on Thursday afternoon?\" or \"Add lunch with Sam on Friday at 1\". olá reads your calendar only when you ask, and changes only the events created from your watch.",
  },
  {
    icon: "search",
    title: "Live answers",
    short: "Weather, news, opening hours and more.",
    long: "For things that change, olá looks them up: today's weather, the news, opening hours, addresses, travel updates and current prices.",
  },
  {
    icon: "globe",
    title: `${languageCountLabel} languages`,
    short: "Or choose Auto: it answers in the language you speak.",
    long: `Pick one of ${languageCountLabel} languages, or choose Auto and olá replies in whichever language you speak to it.`,
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
    long: "Switch the watch between its blue and white themes, on the watch, in your olá account or just by asking.",
  },
  {
    icon: "history",
    title: "Searchable history",
    short: "Search everything you asked and every answer. Delete anytime.",
    long: "Your conversation history lives in your olá account. Search it for anything you asked or anything olá answered, export it, or delete it at any time.",
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

// The six cards under the home page hero (the integrations link to their section of /integrations/).
// Each card: one title line and exactly two text lines (each line short enough for phone width).
export const highlights: { icon: IconName; title: string; text: [string, string]; href?: string }[] = [
  { icon: "mic", title: "Voice Assistant", text: ["Natural conversations,", "just tap and talk."], href: "/features/" },
  { icon: "calendar", title: "Calendar", text: ["Works with Google", "and Outlook Calendar."], href: "/integrations/" },
  { icon: "clock", title: "Long-term Memory", text: ["Remembers everything", "you talk about."], href: "/features/" },
  { icon: "mailSend", title: "Note to Email", text: ["Dictate a note, then", "send it to your inbox."], href: "/integrations/#note-to-email" },
  { icon: "bell", title: "Smart Reminders", text: ["Time, place, people", "and early alerts."], href: "/features/" },
  { icon: "search", title: "Live Answers", text: ["Weather, news, opening", "hours and more."], href: "/features/" },
];
