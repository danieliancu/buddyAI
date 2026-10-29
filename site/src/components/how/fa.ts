/* Font Awesome 5 glyphs used by the simulated watch screens (the "OlaFA" font of watch-screen.css),
 * the same code points as the firmware's icon font. */
const g = (cp: number) => String.fromCodePoint(cp);

export const FA = {
  mic: g(0xf130), right: g(0xf054), pin: g(0xf08d), file: g(0xf15c), cal: g(0xf073), bell: g(0xf0f3),
  trash: g(0xf1f8), check: g(0xf00c), pen: g(0xf304), close: g(0xf00d), wifi: g(0xf1eb), batt: g(0xf241),
  gear: g(0xf013),
};
