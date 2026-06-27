import { test, expect } from "@playwright/test";
import { enterBaselineKb } from "./fixtures";

test("GraphView renders a canvas with the baseline graph", async ({ page }) => {
  await enterBaselineKb(page);
  await page.getByRole("link", { name: "图谱", exact: true }).click();
  await expect(page).toHaveURL(/\/kbs\/1\/graph/);
  // Not the empty state (baseline KB has indexed data)
  await expect(page.getByText(/先触发一次索引任务/)).toHaveCount(0);
  // react-force-graph-2d renders a <canvas> element
  const canvas = page.locator("canvas").first();
  await expect(canvas).toBeVisible({ timeout: 15000 });
  const box = await canvas.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(100);
  expect(box!.height).toBeGreaterThan(100);
  // Clicking the canvas does not throw; the page stays usable
  await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 } });
  await expect(canvas).toBeVisible();
});
