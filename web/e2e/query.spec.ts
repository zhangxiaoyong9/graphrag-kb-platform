import { test, expect } from "@playwright/test";
import { enterBaselineKb } from "./fixtures";

test("run a local query and see the canned answer", async ({ page }) => {
  await enterBaselineKb(page);
  await page.getByRole("link", { name: "检索问答", exact: true }).click();
  await expect(page).toHaveURL(/\/kbs\/1\/query/);

  // Method button's accessible name includes its description, so match by prefix.
  await page.getByRole("button", { name: /^local\b/ }).click();
  await page.getByPlaceholder(/输入你的问题/).fill("hello e2e");
  await page.getByRole("button", { name: "提问" }).click();

  // FakeQueryEngine returns "[local] You asked: hello e2e"
  await expect(page.getByText("[local] You asked: hello e2e")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("回答", { exact: true })).toBeVisible();
});
