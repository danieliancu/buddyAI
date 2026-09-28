// Read the server's language registry at build time (single source of truth).
import data from "../../../server/config/languages.json";

export interface Language {
  code: string;
  name: string;
  native_name: string;
  script: string;
  rtl: boolean;
}

export const languages: Language[] = [...(data.languages as Language[])].sort((a, b) =>
  a.name.localeCompare(b.name, "en"),
);

/** Exact number of languages in the registry (excluding "Auto"). */
export const languageCount = languages.length;

/** Rounded-down marketing figure, e.g. 57 -> "50+". */
export const languageCountLabel = `${Math.floor(languageCount / 10) * 10}+`;
