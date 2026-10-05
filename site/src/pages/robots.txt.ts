import type { APIRoute } from "astro";

// Every public page may be crawled (search engines, including Googlebot and OAI-SearchBot). /thank-you/ is not
// blocked on purpose: crawlers must fetch it to see its noindex. Private areas are protected by sign-in, not here.
export const GET: APIRoute = ({ site }) => {
  const sitemap = new URL("/sitemap-index.xml", site).href;
  return new Response(`User-agent: *\nAllow: /\n\nSitemap: ${sitemap}\n`, {
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
};
