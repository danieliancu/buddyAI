// Read the server's language registry at build time (single source of truth).
import data from "../../../server/config/languages.json";

export interface Language {
  code: string;
  name: string;
  native_name: string;
  script: string;
  rtl: boolean;
}

// The languages offered on the watch: the ones its menus are translated into (English, the European
// languages and Ukrainian). Keep in sync with WATCH_UI_LANGUAGES in server/app/languages.py.
const WATCH_LANGUAGES = new Set([
  "en", "ro", "de", "fr", "es", "it", "pt", "nl", "ca", "gl",
  "sv", "da", "no", "is", "fi", "et", "lv", "lt", "cy",
  "pl", "cs", "sk", "hu", "sl", "hr", "bs", "sr",
  "el", "bg", "mk", "uk",
]);

export const languages: Language[] = (data.languages as Language[])
  .filter((l) => WATCH_LANGUAGES.has(l.code))
  .sort((a, b) => a.name.localeCompare(b.name, "en"));

/** Exact number of languages in the registry (excluding "Auto"). */
export const languageCount = languages.length;

/** Rounded-down marketing figure, e.g. 57 -> "50+". */
export const languageCountLabel = `${Math.floor(languageCount / 10) * 10}+`;
