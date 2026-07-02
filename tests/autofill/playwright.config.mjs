import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "harness.spec.mjs",
  timeout: 90_000,
  workers: 1,
  use: { headless: true },
  webServer: {
    command: "node fixture-server.mjs",
    url: "http://127.0.0.1:8766/test/generic.html",
    reuseExistingServer: true,
    timeout: 15_000,
  },
});
