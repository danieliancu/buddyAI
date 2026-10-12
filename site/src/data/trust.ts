/* Content of /trust/ (Trust & compliance). Every claim has a status, and the source it was checked against
 * is noted beside it (repository paths are for us; they are not shown on the page). Keep the wording
 * modest: no certification, no end-to-end encryption, nothing "impossible". Re-check before each update. */

export type Status = "active" | "firmware" | "progress" | "planned" | "unverified";

export const STATUS: Record<Status, { label: string; definition: string }> = {
  active: { label: "Active", definition: "Checked in the configuration we actually run in production." },
  firmware: { label: "Implemented in firmware", definition: "In the watch's firmware code; rollout to units not confirmed yet." },
  progress: { label: "In progress", definition: "Work or testing has clearly started." },
  planned: { label: "Planned", definition: "A future step we have committed to, not active yet." },
  unverified: { label: "Not yet verified", definition: "Information or documents we haven't confirmed yet." },
};
export const STATUS_ORDER: Status[] = ["active", "firmware", "progress", "planned", "unverified"];

/** When the claims below were last checked against the code, the production configuration and our documents. */
export const lastReviewed = "2026-10-12";

export type Group = "connections" | "firmware" | "data" | "compliance";
export const GROUPS: Record<Group, { title: string; text: string }> = {
  connections: { title: "Connections", text: "How the watch, the website and your account talk to our service." },
  firmware: { title: "Watch firmware", text: "Protections built into the software on the watch." },
  data: { title: "Data protection", text: "How your account and conversations are handled on our side." },
  compliance: { title: "Compliance documents", text: "Product testing, registrations and declarations." },
};

export interface Item {
  title: string;
  text: string;
  status: Status;
  group: Group;
}

export const items: Item[] = [
  // --- connections ---------------------------------------------------------------------------------------
  // Live checks (curl, 2026-10-12): http:// redirects to https:// on www., app. and api.; certificates valid;
  // Strict-Transport-Security max-age=31536000; includeSubDomains on all three.
  {
    group: "connections",
    status: "active",
    title: "HTTPS for the website and your account",
    text: "Our website, your olá account and the service the watch connects to are served over HTTPS with valid certificates. Plain HTTP requests are redirected to HTTPS, and browsers are told to keep using HTTPS (HSTS).",
  },
  // Live checks: Content-Security-Policy, X-Frame-Options DENY on app.; nosniff and Referrer-Policy on all.
  {
    group: "connections",
    status: "active",
    title: "Browser security headers",
    text: "Your olá account sends a Content Security Policy and can't be embedded in other sites. Our sites also send headers that stop browsers guessing file types and limit what is shared when you follow a link.",
  },
  // firmware/sdkconfig.release (wss:// URL, mDNS off); protocol_client.c rejects non-wss URLs in release builds;
  // CA bundle: sdkconfig.defaults CONFIG_MBEDTLS_CERTIFICATE_BUNDLE.
  {
    group: "connections",
    status: "firmware",
    title: "Encrypted connection from the watch",
    text: "Release firmware connects to our service only over TLS (wss://), checks the server's certificate against a built-in list of trusted certificate authorities, and uses a fixed server address that can't be changed during setup.",
  },

  // --- firmware ------------------------------------------------------------------------------------------
  // sdkconfig.release: signed apps (RSA-3072, Secure Boot V2 scheme), HTTPS-only OTA; ota.c SHA-256 + size check;
  // rollback enabled.
  {
    group: "firmware",
    status: "firmware",
    title: "Signed software updates",
    text: "Release firmware installs updates only over HTTPS and only if they carry a valid RSA-3072 signature from our signing key. Each download is checked against its SHA-256 hash, and an update that fails to start rolls back to the previous version.",
  },
  // net_portal.c WIFI_AUTH_WPA2_PSK; prov_util.c 8-character password from esp_random; renewed after setup.
  {
    group: "firmware",
    status: "firmware",
    title: "Protected Wi-Fi setup network",
    text: "During Wi-Fi setup the watch's temporary network is protected with WPA2 and a random 8-character password shown on the watch. A new password is created after each successful setup.",
  },
  // ble_prov.c + sdkconfig.defaults: protocomm Security 2 only (SRP6a, AES-256-GCM); pause after 3 failed sessions.
  {
    group: "firmware",
    status: "firmware",
    title: "Bluetooth setup for Android",
    text: "Android phones send your Wi-Fi details to the watch over Bluetooth Low Energy using ESP-IDF's Security 2 scheme: the setup password shown on the watch is used for a password-authenticated key exchange (SRP6a), and the details are then encrypted with AES-256-GCM. Bluetooth setup pauses after repeated wrong passwords.",
  },
  // audio.c / protocol_client.c: Opus frames sent only during an active listening turn; max_listen_s; no local recording.
  {
    group: "firmware",
    status: "firmware",
    title: "Microphone audio only when you ask",
    text: "The watch sends microphone audio only during a conversation turn, after you press the top button or tap the mic, and each turn is time-limited. Voice is not recorded to the watch's storage.",
  },
  // main/debug_snap.c and diagnostics compiled out by CONFIG_BUDDYAI_RELEASE_BUILD; release log level WARN.
  {
    group: "firmware",
    status: "firmware",
    title: "Development tools left out of release builds",
    text: "Tools we use while developing the watch, such as screen capture, are not included in release builds, which also keep less detail in their logs.",
  },
  // firmware/README.md §8 (production units only); confirmed as the plan by the founder, 2026-10-12.
  {
    group: "firmware",
    status: "planned",
    title: "Secure Boot",
    text: "Secure Boot V2 on the units we sell, so the watch starts only software signed by us. It is not enabled in current builds.",
  },
  {
    group: "firmware",
    status: "planned",
    title: "Flash and settings encryption",
    text: "Encrypting the watch's flash memory and its settings storage, which holds your Wi-Fi password and the watch's access token. Until this is enabled, these are stored on the watch without encryption.",
  },
  {
    group: "firmware",
    status: "planned",
    title: "Locking the chip's debug and download interfaces",
    text: "Restricting the debug (JTAG) and firmware download interfaces on the units we sell.",
  },

  // --- data ----------------------------------------------------------------------------------------------
  // server/app/security.py (bcrypt); ratelimit.py (sign-in limits). Production runs master (Coolify).
  {
    group: "data",
    status: "active",
    title: "Passwords",
    text: "Account passwords are stored only as bcrypt hashes, never in readable form. Repeated failed sign-in attempts are slowed down.",
  },
  // security.py token_urlsafe(32), SHA-256 hash stored; config.py pairing_code_ttl_s=300; hub.py revoke.
  {
    group: "data",
    status: "active",
    title: "Watch pairing and access",
    text: "Each watch is paired with a 6-digit code shown on its screen, valid for 5 minutes, and then receives its own access token; we keep only a hash of that token. Removing a watch from your account revokes its access.",
  },
  // main.py SessionMiddleware: same_site=strict, HttpOnly, https_only with an https public URL; session_version.
  {
    group: "data",
    status: "active",
    title: "Sign-in sessions",
    text: "Sign-in uses cookies that page scripts can't read, that are sent only over HTTPS and never from other sites. Changing your password signs you out everywhere else.",
  },
  // billing.py: Stripe Checkout and billing portal; only Stripe IDs stored.
  {
    group: "data",
    status: "active",
    title: "Card payments",
    text: "Card details are entered on Stripe's hosted checkout and never reach our servers.",
  },
  // No audio column or file writes for audio (stt/openai.py keeps the fallback WAV in memory).
  {
    group: "data",
    status: "active",
    title: "No voice recordings kept on our servers",
    text: "Your voice is processed in memory and passed to our AI provider; we don't store recordings of it. We do keep the text: see “How your data moves”.",
  },
  // api/me.py: DELETE conversations, GET export, DELETE account (password required).
  {
    group: "data",
    status: "active",
    title: "Your controls",
    text: "From your olá account you can delete your conversation history, download a copy of your data, switch off olá's memory, or delete your account.",
  },
  // models.py audit_log; api/devices.py history.view / history.delete.
  {
    group: "data",
    status: "active",
    title: "Staff access to conversations is logged",
    text: "When a member of our team opens or deletes a customer's conversation history in our admin tools, it is recorded in an audit log.",
  },
  // deploy/README.md §10: sign OpenAI's Data Processing Addendum.
  {
    group: "data",
    status: "planned",
    title: "Data processing agreement with our AI provider",
    text: "Signing a data processing agreement with OpenAI for the processing it does on our behalf.",
  },
  // No retention setting found on the provider requests.
  {
    group: "data",
    status: "unverified",
    title: "How long our AI provider keeps data",
    text: "OpenAI's handling of the data it processes for us is governed by its API terms. We haven't yet confirmed a retention setting with them.",
  },
  // No evidence of database or backup encryption at rest in server/ or deploy/.
  {
    group: "data",
    status: "unverified",
    title: "Encryption at rest",
    text: "We haven't verified encryption at rest for our database and backups, so we don't claim it.",
  },
  {
    group: "data",
    status: "unverified",
    title: "Independent security testing",
    text: "No independent security assessment or penetration test has been completed yet. There are no results to publish.",
  },

  // --- compliance ----------------------------------------------------------------------------------------
  // deploy/README.md §10: "UKCA/CE marking, RED (radio) test report, WEEE and battery registration".
  {
    group: "compliance",
    status: "planned",
    title: "UKCA and CE conformity",
    text: "Conformity assessment for the UK and EU, including a radio equipment (RED) test report. Until it is complete we don't claim UKCA or CE conformity.",
  },
  {
    group: "compliance",
    status: "planned",
    title: "WEEE registration",
    text: "Registering as a producer of electrical equipment and setting up take-back for old devices (WEEE).",
  },
  {
    group: "compliance",
    status: "planned",
    title: "Battery producer registration",
    text: "Registering as a producer of the watch's battery.",
  },
  // deploy/README.md §10 + TODO: ICO data protection fee.
  {
    group: "compliance",
    status: "planned",
    title: "ICO registration",
    text: "Registering with the UK Information Commissioner's Office and paying the data protection fee.",
  },
  // deploy/README.md §10: "Measured battery life, published on the site". firmware/README.md §10 has the method.
  {
    group: "compliance",
    status: "planned",
    title: "Measured battery life",
    text: "Measuring the watch's battery life with a repeatable test and publishing the result here.",
  },
  // Nothing found in the repository: no datasheet, MSDS or test reports.
  {
    group: "compliance",
    status: "unverified",
    title: "Battery safety documents",
    text: "The battery datasheet and safety and transport test reports (such as IEC 62133 and UN 38.3) haven't been confirmed yet.",
  },
  {
    group: "compliance",
    status: "unverified",
    title: "Restricted substances",
    text: "Declarations covering restricted substances (RoHS) and chemicals (REACH) haven't been confirmed yet.",
  },
];

/** Watch specifications: board and parts from the firmware's board configuration
 * (firmware/components/board/include/board_pins.h, sdkconfig.defaults, dependencies.lock). */
export interface Spec {
  label: string;
  value: string;
  status: Status;
  note?: string;
}
export const specs: Spec[] = [
  { label: "Board", value: "Waveshare ESP32-S3-Touch-AMOLED-2.06", status: "firmware" },
  { label: "Processor", value: "Espressif ESP32-S3 (ESP32-S3R8)", status: "firmware" },
  { label: "Memory", value: "8 MB PSRAM, 32 MB flash", status: "firmware" },
  { label: "Display", value: "AMOLED, 410 × 502 pixels (CO5300 controller)", status: "firmware" },
  { label: "Touch", value: "Capacitive touch (FT3168)", status: "firmware" },
  { label: "Audio", value: "Speaker with ES8311 codec and NS4150B amplifier; two microphones with ES7210 converter", status: "firmware" },
  { label: "Power and clock", value: "AXP2101 power management, PCF85063 real-time clock", status: "firmware" },
  { label: "Motion sensor", value: "QMI8658 on the board, not used by the firmware", status: "firmware" },
  { label: "Wi-Fi", value: "2.4 GHz", status: "firmware" },
  { label: "Bluetooth", value: "Bluetooth Low Energy, used only during Wi-Fi setup", status: "firmware" },
  { label: "Software", value: "ESP-IDF 5.5.4, LVGL 9.5.0", status: "firmware" },
  // firmware/README.md and a code comment only: no model, datasheet or BOM found.
  { label: "Battery", value: "Lithium-polymer, 320 mAh (3.7 V nominal)", status: "unverified", note: "Model and datasheet not confirmed yet." },
  // board_core.c: 150 mA, 4.2 V, 25 mA termination; to be checked against the battery datasheet.
  { label: "Charging", value: "Set in firmware to 150 mA up to 4.2 V", status: "unverified", note: "To be checked against the battery datasheet." },
  // firmware/README.md §10: target, not measured.
  { label: "Battery life", value: "Test target: 8 hours of typical use", status: "unverified", note: "Not measured yet, and not a promise." },
];

/** How a conversation moves (server/app/providers: OpenAI profile is the production default). */
export const flow: { title: string; text: string }[] = [
  { title: "You press", text: "Nothing is sent until you press the top button or tap the mic on the watch." },
  { title: "From the watch", text: "Your voice is compressed and streamed to our service over an encrypted (TLS) connection." },
  {
    title: "Our server and OpenAI",
    text: "Our server passes the audio to our AI provider, OpenAI, to turn it into text, then asks OpenAI's language model for a reply, using your recent conversation, your persona and relevant saved memories. For questions about current events, OpenAI's web search may be used, with your approximate city.",
  },
  { title: "The reply", text: "OpenAI turns the reply into speech. The watch plays it and shows the words on screen." },
];
