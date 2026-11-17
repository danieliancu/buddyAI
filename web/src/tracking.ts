/** Public tracking page for a parcel, or null when we have no link for that carrier. Only Royal Mail for now:
 * an empty carrier counts as Royal Mail, since that is what we ship with. */
export function trackingUrl(carrier: string | null | undefined, number: string | null | undefined): string | null {
  const n = (number ?? "").trim();
  if (!n) return null;
  const c = (carrier ?? "").trim().toLowerCase();
  if (c && !c.replace(/\s+/g, " ").includes("royal mail")) return null;
  return `https://www.royalmail.com/track-your-item#/tracking-results/${encodeURIComponent(n.replace(/\s+/g, ""))}`;
}
