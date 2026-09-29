import { defineConfig } from "@playwright/test";

// End-to-end demo flow. Requires the full stack (docker compose up) on BASE_URL.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1600, height: 1000 },
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  reporter: [["list"]],
});
