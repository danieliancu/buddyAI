// The product names as one word. In markup (brandHtml): in running text "olacompanion" is bold, in the text
// colour; with `bold` (headings) it is set like the logo, "companion" in the accent colour and the leaf above the "a"; "olacare" bold, in the text colour.
// brandText() is the plain form for titles, alt texts and meta data.
export type BrandName = "watch" | "care";

const LEAF =
  '<svg viewBox="0 0 16 16" aria-hidden="true" class="absolute -top-[2px] left-[2px] h-[0.55em] w-[0.55em] text-accent">' +
  '<path d="M2.5 13.5C2.5 7 6.8 2.6 14 2c.4 7-4.2 11.5-11.5 11.5z" fill="currentColor"/>' +
  '<path d="M2.5 13.5 9.5 6.5" stroke="#fff" stroke-width="1.1" stroke-linecap="round"/></svg>';

export function brandHtml(name: BrandName, opts: { bold?: boolean } = {}): string {
  if (name === "care") {
    return '<strong class="whitespace-nowrap font-bold">olácare</strong>';
  }
  if (!opts.bold) return '<strong class="whitespace-nowrap font-bold">olácompanion</strong>';
  return `<span class="whitespace-nowrap font-bold">ol<span class="relative">a${LEAF}</span><span class="text-accent">companion</span></span>`;
}

export function brandText(name: BrandName): string {
  return name === "watch" ? "olácompanion" : "olácare";
}
