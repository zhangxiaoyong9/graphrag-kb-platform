import { test, expect } from "@playwright/test";
import { enterBaselineKb } from "./fixtures";

test("entity/relation page lists entities and filters relations on select", async ({ page }) => {
  await enterBaselineKb(page);
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await page.getByRole("link", { name: /查看文档 baseline/ }).first().click();
  await page.getByRole("link", { name: /实体/ }).click();
  await expect(page).toHaveURL(/\/kbs\/1\/documents\/1\/entities/);
  await expect(page.getByText("实体 / 关系")).toBeVisible({ timeout: 15000 });

  // At least one entity card (button with accessible name "查看实体 ... 的关系")
  const firstEntity = page.getByRole("button", { name: /查看实体 .* 的关系/ }).first();
  await expect(firstEntity).toBeVisible();
  await firstEntity.click();
  await expect(page.getByText(/已选择实体：/)).toBeVisible();
});
