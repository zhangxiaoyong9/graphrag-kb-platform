import { test, expect } from "@playwright/test";
import { createKbViaApi, enterKbById, uniqueKbName } from "./fixtures";

test("paste a document and see it listed", async ({ page }) => {
  const kbId = await createKbViaApi(page, uniqueKbName("paste"));
  await enterKbById(page, kbId);
  // Land on the KB overview; go to the documents tab
  await page.getByRole("link", { name: "文档", exact: true }).click();
  await page.getByPlaceholder("标题（可选）").fill("pasted.txt");
  await page.getByPlaceholder(/在此粘贴正文内容/).fill("Hello E2E pasted document body text.");
  await page.getByRole("button", { name: "添加文档" }).click();
  await expect(page.getByText("pasted.txt")).toBeVisible({ timeout: 15000 });
});
