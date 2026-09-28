// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";

// Dev only: where `npm run dev` proxies /api/* (the BuddyAI server). In production Caddy
// proxies /api/shop/* on the www domain to the server, so the built site uses same-origin URLs.
const API_TARGET = process.env.BUDDYAI_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  // TODO(owner): set the real public URL of the marketing site (used for canonical URLs, sitemap, OG tags).
  site: "https://www.example.com",
  output: "static",
  trailingSlash: "ignore",
  build: { format: "directory" },
  integrations: [
    sitemap({
      filter: (page) => !page.includes("/thank-you") && !page.includes("/404"),
      i18n: undefined,
    }),
  ],
  vite: {
    plugins: [tailwindcss()],
    server: {
      proxy: { "/api": { target: API_TARGET, changeOrigin: true } },
    },
  },
});
