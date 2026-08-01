/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  test: {
    environment: "happy-dom",
    // Playwright owns e2e/ and e2e-live/ — keep vitest's default *.spec.ts
    // pickup out of both.
    exclude: ["**/node_modules/**", "e2e/**", "e2e-live/**"],
  },
});
