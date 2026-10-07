// Signed-in state for the site header: the ola account (another subdomain) tells our own site whether the
// visitor is signed in and their first name (GET /api/me/badge, CORS for this origin only). Any error = signed out.

export interface Badge {
  signedIn: boolean;
  name: string;
}

export function parseBadge(data: unknown): Badge {
  const o = (data && typeof data === "object" ? data : {}) as { signed_in?: unknown; name?: unknown };
  const name = typeof o.name === "string" ? o.name.trim().slice(0, 24) : "";
  return o.signed_in === true && name ? { signedIn: true, name } : { signedIn: false, name: "" };
}

export async function fetchBadge(appUrl: string): Promise<Badge> {
  try {
    const r = await fetch(`${appUrl.replace(/\/$/, "")}/api/me/badge`, {
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    return r.ok ? parseBadge(await r.json()) : { signedIn: false, name: "" };
  } catch {
    return { signedIn: false, name: "" };
  }
}
