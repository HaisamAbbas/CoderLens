import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy the API to the FastAPI server so the app is same-origin.
// 8000 is the API port everywhere else (README, Dockerfile's PORT default,
// uvicorn's own default) — a mismatch here surfaces as a bare
// "500 Internal Server Error" on every /api call, because the dev proxy
// answers 500 on a refused connection rather than reporting the real cause.
// Override with API_PORT when running the backend somewhere else.
const API_PORT = process.env.API_PORT ?? "8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": `http://127.0.0.1:${API_PORT}`,
    },
  },
  build: { outDir: "dist" },
});
