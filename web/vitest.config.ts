import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Unit and component tests run in jsdom against the real modules -- no mock
 * server. The API client's tests stub `fetch` at the network boundary, which is
 * the only honest seam for asserting request shape; behaviour against the actual
 * backend is verified separately, end to end.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": new URL(".", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1") },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./test/setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
    css: false,
  },
});
