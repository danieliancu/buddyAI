import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Backend (FastAPI) for `npm run dev`. Override with BUDDYAI_BACKEND=http://127.0.0.1:8799
const backend = process.env.BUDDYAI_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: false },
      "/ws": { target: backend, ws: true },
      "/fw": { target: backend },
    },
  },
});
