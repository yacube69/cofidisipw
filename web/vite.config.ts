/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Python API runs separately (uvicorn src.api:app --port 8000); Vite forwards /api to it.
// In the Docker image the API serves the built app from web/dist, so both share one origin there.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "jsdom" },
});
