import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api and /health are proxied to the FastAPI backend so no CORS setup is needed.
const backend = process.env.VITE_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": backend, "/health": backend },
  },
});
