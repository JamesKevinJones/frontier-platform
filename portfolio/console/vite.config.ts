import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// One console, three backends. The proxy keeps the browser on a single origin so
// there is no CORS negotiation in the demo path and no per-service base URL to
// configure at build time.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/rag": {
        target: "http://127.0.0.1:8001",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/rag/, ""),
      },
      "/api/agents": {
        target: "http://127.0.0.1:8002",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/agents/, ""),
      },
      "/api/guard": {
        target: "http://127.0.0.1:8003",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api\/guard/, ""),
      },
    },
  },
});
