import { expect, test, type Page } from "@playwright/test";

const SHOTS = process.env.SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function login(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(process.env.DEMO_PASSWORD ?? "veriops-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Operations dashboard" })).toBeVisible();
}

test("flagship demo: unsafe scaling rejected, rollback approved, service recovered", async ({ page }) => {
  await login(page, "alice");
  await shot(page, "01-dashboard");

  // Trigger the flagship incident (auto-investigates for roles that may investigate).
  const flagship = page.locator("div", { hasText: "flagship" }).getByRole("button", { name: "Trigger" }).first();
  await flagship.click();
  await expect(page).toHaveURL(/\/incidents\/INC-/);

  // The AI's scaling recommendation is rejected by deterministic validation.
  await expect(page.getByText("AI RECOMMENDATION REJECTED").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Human approval required").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Roll back deployment #842 of checkout-service").first()).toBeVisible();
  await shot(page, "02-approval-requested");

  await page.getByRole("tab", { name: /Hypotheses/ }).click();
  await shot(page, "03-hypotheses");
  await page.getByRole("tab", { name: /Validation/ }).click();
  await shot(page, "04-validation");

  await page.getByPlaceholder(/Comment/).fill("Validated in sandbox; approve rollback of #842");
  await page.getByRole("button", { name: /Approve & execute/ }).click();
  await expect(page.getByText("RESOLVED").first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("tab", { name: /Timeline/ }).click();
  await expect(page.getByText(/Service recovered/).first()).toBeVisible({ timeout: 15_000 });
  await shot(page, "05-resolved-timeline");
  await page.getByRole("tab", { name: /Audit log/ }).click();
  await expect(page.getByText("approval.approved").first()).toBeVisible();
  await shot(page, "06-audit");
  await page.getByRole("tab", { name: /AI cost/ }).click();
  await shot(page, "07-cost");
  await page.getByRole("tab", { name: /Report/ }).click();
  await expect(page.getByText("Rejected remediations")).toBeVisible();
  await shot(page, "08-report");
});

test("viewer cannot approve or trigger incidents", async ({ page }) => {
  await login(page, "victor");
  await expect(page.getByText("Your role cannot create incidents.")).toBeVisible();
});
