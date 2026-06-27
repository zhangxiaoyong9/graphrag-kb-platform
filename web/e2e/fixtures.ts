import type { Page } from "@playwright/test";

const PREFIX = "e2e-kb";

/** Unique KB name for state-changing specs (never collides with the baseline). */
export function uniqueKbName(label = "kb"): string {
  return `${PREFIX}-${label}-${Date.now()}`;
}

/** Create a KB via the REST API and return its id. */
export async function createKbViaApi(page: Page, name: string): Promise<number> {
  const r = await page.request.post("/kbs", { data: { name, method: "standard" } });
  const body = await r.json();
  return body.id as number;
}

/**
 * Navigate into a KB workspace via in-SPA links.
 *
 * The backend API routes (GET /kbs/{id}, /kbs/{id}/documents/{doc_id},
 * /kbs/{id}/graph, ...) shadow the SPA paths, so a hard page.goto to any
 * /kbs/:id/* URL returns raw JSON instead of booting the SPA. Reach a KB
 * workspace by clicking links (client-side routing) from the dashboard.
 */
export async function enterKbById(page: Page, kbId: number): Promise<void> {
  await page.goto("/");
  await page.getByRole("link", { name: "知识库管理" }).click();
  // The dashboard also surfaces a recent-job link containing the KB name
  // (e.g. /kbs/1/jobs/1), so target the KB-workspace link by its exact href.
  await page.locator(`a[href="/kbs/${kbId}"]`).first().click();
}

/** Convenience: enter the seeded baseline KB (id 1). */
export async function enterBaselineKb(page: Page): Promise<void> {
  await enterKbById(page, 1);
}
