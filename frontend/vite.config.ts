import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy the API to the FastAPI server so the app is same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
  build: { outDir: "dist" },
});
