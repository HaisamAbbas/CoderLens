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
      // changeOrigin: true rewrites the Host header to the target's — without
      // it, the backend sees "Host: localhost:5173" (the browser's original
      // request), and routers/auth.py builds the GitHub OAuth callback URL
      // from that Host header (`request.url_for(...)`) to work in both dev
      // and prod without a separate setting. That mismatch makes GitHub
      // reject the login with "redirect_uri is not associated with this
      // application" — the callback URL registered on GitHub is the real
      // backend's (:8000), never the Vite dev server's. Target "localhost",
      // not "127.0.0.1": changeOrigin copies the target's host verbatim into
      // that header, and it must match the callback URL registered on GitHub
      // exactly, string-for-string — "localhost" and "127.0.0.1" are two
      // different hosts as far as that exact-match check is concerned, even
      // though they reach the same machine.
      "/api": { target: `http://localhost:${API_PORT}`, changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
