// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Dev only: where `npm run dev` proxies /api/* (the ola server). In production Caddy
// proxies /api/shop/* on the www domain to the server, so the built site uses same-origin URLs.
const API_TARGET = process.env.BUDDYAI_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  // Public URL of the marketing site (canonical URLs, sitemap, Open Graph, structured data).
  site: "https://www.olawatch.ai",
  output: "static",
  trailingSlash: "ignore",
  build: { format: "directory" },
  integrations: [
    // Indexable pages only (thank-you and 404 are noindex). No lastmod: a build date is not a content change.
    sitemap({
      filter: (page) => !page.includes("/thank-you") && !page.includes("/404"),
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: { "/api": { target: API_TARGET, changeOrigin: true } },
    },
  },
});
