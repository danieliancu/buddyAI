import { languageCountLabel } from "../lib/languages";

export type IconName =
  | "mic" | "tap" | "globe" | "persona" | "palette" | "history" | "display" | "speaker" | "wifi" | "update";

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
    title: "Your colours",
    short: "Custom colours and themes for your screen.",
    long: "Make the watch yours with custom colours and themes for the display.",
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
  { icon: "mic" as IconName, title: "Built-in microphones", text: "Just raise your wrist and speak naturally." },
  { icon: "speaker" as IconName, title: "Built-in speaker", text: "Replies are spoken out loud, straight from the watch." },
  { icon: "wifi" as IconName, title: "Wi-Fi + over-the-air updates", text: "Connects over Wi-Fi and gets firmware updates over the air." },
];

export const privacyLine =
  "Your voice is processed securely by our AI partners (OpenAI) to answer you. We never sell your data, and you can delete your history anytime.";
