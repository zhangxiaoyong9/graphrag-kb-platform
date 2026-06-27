import { test, expect } from "@playwright/test";
import { createKbViaApi, enterKbById, uniqueKbName } from "./fixtures";

test("trigger a full job and watch it succeed", async ({ page }) => {
  const kbId = await createKbViaApi(page, uniqueKbName("job"));
  // Seed a doc via the API (worker needs something to index)
  await page.request.post(`/kbs/${kbId}/documents`, {
    data: { title: "job.md", text: "ACME Org Bob Person Foo Bar " + "x ".repeat(50) },
  });
  await enterKbById(page, kbId);
  await page.getByRole("button", { name: "全量索引" }).click();
  // "已创建任务 #N" confirms the trigger fired
  await expect(page.getByText(/已创建任务 #\d+/)).toBeVisible({ timeout: 15000 });

  // Poll the jobs API until the background fake worker marks the job succeeded
  await expect.poll(
    async () => {
      const r = await page.request.get(`/kbs/${kbId}/jobs`);
      const jobs = await r.json();
      return jobs.some((j: { status: string }) => j.status === "succeeded");
    },
    { timeout: 60000, message: "triggered job to reach succeeded" },
  ).toBe(true);
});
