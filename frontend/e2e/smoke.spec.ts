import { test, expect } from "@playwright/test";

// Smoke: a user message round-trips through the gateway and the assistant
// response (tokens + a tool card) renders in the chat panel.
test("user message → assistant tokens + tool card", async ({ page }) => {
  await page.goto("/");
  // session/transport connect
  await expect(page.getByText(/session:/)).toBeVisible({ timeout: 15_000 });

  await page.getByPlaceholder(/Ask myAgent/).fill("list the skills");
  await page.getByRole("button", { name: "Send" }).click();

  // an assistant bubble appears (token stream) and a tool card for the
  // skills-listing tool renders.
  await expect(page.locator("text=🔧").first()).toBeVisible({ timeout: 20_000 });
});

// WS→SSE downgrade: if the browser can't open the WS, the hook falls back to
// SSE and the header still shows "live". We simulate WS failure by blocking
// the WS route via context route interception, then reload.
test("falls back to SSE when WS is blocked", async ({ page, context }) => {
  await context.route("**/api/sessions/*", (route) => {
    const req = route.request();
    if (req.url().startsWith("ws://") || req.url().startsWith("wss://")) {
      route.abort();
    } else {
      route.continue();
    }
  });
  await page.goto("/");
  // transport label should resolve to sse
  await expect(page.getByText(/sse/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByPlaceholder(/Ask myAgent/)).toBeEnabled();
});
