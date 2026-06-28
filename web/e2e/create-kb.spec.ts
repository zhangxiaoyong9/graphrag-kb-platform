import { test, expect } from "@playwright/test";
import { uniqueKbName } from "./fixtures";

test("create a KB via the form and see it in the list", async ({ page }) => {
  const name = uniqueKbName("create");
  await page.goto("/");
  await page.getByRole("link", { name: "知识库管理" }).click();
  // An LLM provider profile is required (seeded by the e2e harness);
  // index 0 is the "请选择…" placeholder, index 1 is the first real profile.
  await page.getByLabel(/LLM 配置/).selectOption({ index: 1 });
  await page.getByPlaceholder("请输入知识库名称").fill(name);
  await page.getByRole("button", { name: /创建知识库/ }).click();
  // The new KB appears as a workspace link in the management list
  await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible({ timeout: 15000 });
});
