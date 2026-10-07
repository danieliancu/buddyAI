/** Input checks shared with the watch (firmware/components/prov_util/prov_util.c). */

export const SETUP_PASS_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
export const SETUP_PASS_LEN = 8;

/** "k7p4 m9xq" / "K7P4-M9XQ" -> "K7P4M9XQ" (people type what they read, with spaces). */
export function normalizeSetupPassword(input: string): string {
  return input.toUpperCase().replace(/[\s-]/g, "");
}

export function isSetupPassword(pass: string): boolean {
  return pass.length === SETUP_PASS_LEN && [...pass].every((c) => SETUP_PASS_ALPHABET.includes(c));
}

export type WifiCredProblem = "ssid_empty" | "ssid_too_long" | "pass_too_short" | "pass_too_long" | "pass_bad_char" | null;

const byteLength = (s: string) => new TextEncoder().encode(s).length;

/** SSID 1..32 bytes; password empty (open network), 8..63 printable ASCII, or 64 hex digits. */
export function checkWifiCredentials(ssid: string, password: string): WifiCredProblem {
  if (!ssid) return "ssid_empty";
  if (byteLength(ssid) > 32) return "ssid_too_long";
  if (!password) return null;
  if (password.length === 64) return /^[0-9a-fA-F]{64}$/.test(password) ? null : "pass_too_long";
  if (password.length < 8) return "pass_too_short";
  if (password.length > 63) return "pass_too_long";
  if (!/^[\x20-\x7e]+$/.test(password)) return "pass_bad_char";
  return null;
}

export const WIFI_PROBLEM_TEXT: Record<Exclude<WifiCredProblem, null>, string> = {
  ssid_empty: "Choose your Wi-Fi network.",
  ssid_too_long: "That network name is too long for Wi-Fi (32 characters at most).",
  pass_too_short: "Wi-Fi passwords have at least 8 characters. Leave it empty only for an open network.",
  pass_too_long: "Wi-Fi passwords have at most 63 characters.",
  pass_bad_char: "The password has a character Wi-Fi does not allow. Check for emoji or accented letters.",
};
