import { test, expect } from "@playwright/test";
import { enterBaselineKb } from "./fixtures";

async function enterBaselineDocDetail(page: import("@playwright/test").Page) {
  await enterBaselineKb(page);
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await page.getByRole("link", { name: /查看文档 baseline/ }).first().click();
}

test("open a document, verify evidence in the drawer, close keeps body", async ({ page }) => {
  await enterBaselineDocDetail(page);
  await expect(page.getByText("baseline.md")).toBeVisible({ timeout: 15000 });

  // Open the evidence drawer via the first citation
  const citations = page.getByRole("button", { name: /查看证据/ });
  await citations.first().click();
  await expect(page.getByText("证据详情")).toBeVisible();

  // If there is a second citation, switching replaces the drawer content (no second drawer)
  if ((await citations.count()) > 1) {
    await citations.nth(1).click();
    await expect(page.getByText("证据详情")).toBeVisible();
  }

  // Close the drawer; the document body stays
  await page.getByRole("button", { name: "关闭证据抽屉" }).click();
  await expect(page.getByText("证据详情")).toHaveCount(0);
  await expect(page.getByText("baseline.md")).toBeVisible();
});
