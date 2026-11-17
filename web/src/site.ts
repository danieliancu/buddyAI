/** The public website (logo link, "Get a watch"). Override for local development with VITE_SITE_URL. */
export const SITE_URL: string = (import.meta.env.VITE_SITE_URL as string | undefined) || "https://www.olacompanion.com";

/** Customer support address (same as site/src/config.ts supportEmail). */
export const SUPPORT_EMAIL = "ola@olacompanion.com";
