import { test, expect } from "@playwright/test";
import { uniqueKbName } from "./fixtures";

test("create a KB via the form and see it in the list", async ({ page }) => {
  const name = uniqueKbName("create");
  await page.goto("/");
  await page.getByRole("link", { name: "知识库管理" }).click();
  await page.getByPlaceholder("请输入知识库名称").fill(name);
  await page.getByPlaceholder(/^deepseek$/).fill("deepseek");
  await page.getByRole("button", { name: /创建知识库/ }).click();
  // The new KB appears as a workspace link in the management list
  await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible({ timeout: 15000 });
});
