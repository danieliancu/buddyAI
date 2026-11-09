/* Font Awesome 5 glyphs used by the simulated watch screens (the "OlaFA" font of watch-screen.css),
 * the same code points as the firmware's icon font. */
const g = (cp: number) => String.fromCodePoint(cp);

export const FA = {
  mic: g(0xf130), right: g(0xf054), pin: g(0xf08d), file: g(0xf15c), cal: g(0xf073), bell: g(0xf0f3),
  trash: g(0xf1f8), check: g(0xf00c), pen: g(0xf304), close: g(0xf00d), wifi: g(0xf1eb), batt: g(0xf241),
  gear: g(0xf013), vol: g(0xf028),
};

/* Top left of the text screens (conversation, notes, reminders): Aa (larger text) and the speaker (mute), the X's
 * mirror (ui.c ui_add_text_size). */
export const TEXT_TOOLS = `<div class="ws-tools" aria-hidden="true"><div class="ws-aa"><span>Aa</span><span>+</span></div><div class="ws-spk fa">${FA.vol}</div></div>`;

/* The Send by email envelope: drawn, like the firmware's (ui_items.c mail_icon). Styled by .wd-mail. */
export const MAIL_SVG =
  '<svg class="wd-mail" viewBox="0 0 32 24" aria-hidden="true"><rect x="1" y="1" width="30" height="22" rx="4" /><path d="M3 3l13 9.5L29 3" /></svg>';
