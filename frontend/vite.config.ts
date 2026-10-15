import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build goes into the Python package, which FastAPI serves (uatu.web.main: app.frontend).
// In development, the API is the local uvicorn (or UATU_API, e.g. the server through a tunnel).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/uatu/web/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1100, // MapLibre alone is ~1 MB
    rolldownOptions: {
      output: {
        // Libraries apart from Uatu's own code: a new version of the dashboard leaves them in the cache.
        advancedChunks: {
          groups: [
            { name: "maplibre", test: /node_modules[\\/]maplibre-gl/ },
            { name: "react", test: /node_modules[\\/](react|react-dom|scheduler|@tanstack|zustand)[\\/]/ },
          ],
        },
      },
    },
  },
  server: {
    proxy: { "/api": { target: process.env.UATU_API ?? "http://localhost:8000", changeOrigin: false } },
  },
});
