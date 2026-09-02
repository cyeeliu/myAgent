import { defineConfig } from "@playwright/test";

// e2e smoke (spec §11.6): drives the real frontend against the real gateway.
// Run with the stack up:
//   (terminal A) uvicorn agent_gateway.main:app --port 8000   # from /root/myAgent
//   (terminal B) npm run dev                                   # from frontend
//   (terminal C) npx playwright test
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:3000", trace: "on-first-retry" },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
