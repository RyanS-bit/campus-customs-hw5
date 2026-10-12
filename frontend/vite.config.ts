/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard runs at http://localhost:5173 (the origin the backend's CORS settings allow) and calls the
// FastAPI backend at http://localhost:8000 (override with VITE_API_BASE). Tests never contact a server:
// they use recorded backend responses (src/test/fixtures) through a mocked fetch.
export default defineConfig({
  plugins: [react()],
  server: { host: "localhost", port: 5173, strictPort: true },
  preview: { host: "localhost", port: 5173, strictPort: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
