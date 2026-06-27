import { test, expect } from "@playwright/test";
import { enterBaselineKb } from "./fixtures";

test("sidebar group navigation works", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "任务管理" }).click();
  await expect(page).toHaveURL(/\/jobs/);
  await page.getByRole("link", { name: "检索测试" }).click();
  await expect(page).toHaveURL(/\/query/);
  await page.getByRole("link", { name: "知识库管理" }).click();
  await expect(page).toHaveURL(/\/kbs$/);
});

test("KB detail tabs switch", async ({ page }) => {
  await enterBaselineKb(page);
  await expect(page).toHaveURL(/\/kbs\/1$/);
  await page.getByRole("link", { name: "图谱", exact: true }).click();
  await expect(page).toHaveURL(/\/kbs\/1\/graph/);
});
