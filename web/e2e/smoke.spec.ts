import { test, expect } from "@playwright/test";

test("SPA loads, brand visible, app renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("知识库平台", { exact: true })).toBeVisible();
  // The dashboard hero headline confirms the SPA actually booted (not a blank shell)
  await expect(page.getByText(/从非结构化文本到可检索的知识图谱/)).toBeVisible({ timeout: 15000 });
});
