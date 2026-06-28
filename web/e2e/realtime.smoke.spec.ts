/**
 * A1 (WebSocket realtime progress) — browser smoke.
 *
 * The design spec deliberately skipped a Playwright E2E for this feature
 * ("fake server's FakeGraphAdapter job runs too fast to capture intermediate
 * states"). This spec is the complementary *browser-level* smoke: it drives a
 * real headless Chromium against the e2e fake server and proves the three
 * claims the unit tests can't — that a real browser (a) opens the WS and gets a
 * snapshot, (b) receives server-pushed deltas with no manual refresh, and
 * (c) toggles the "实时" tag as the WS connection state changes.
 *
 * Part of the default `npm run e2e` suite (Playwright config auto-starts the fake
 * server). Run just this spec with:
 *   cd web && node_modules/.bin/playwright test e2e/realtime.smoke.spec.ts
 */
import { test, expect } from "@playwright/test";

interface Frame {
  url: string;
  type: string;
  jobStatus?: string;
  steps: { name: string; status: string }[];
}

test("A1 realtime WS — snapshot, live delta, terminal-close fallback", async ({ page }) => {
  // The KB layout also renders an h1, so scope to the job heading by its number.
  const jobHeading = (id: number) => page.getByRole("heading", { name: new RegExp(`任务 #${id}`) });

  const frames: Frame[] = [];
  // Node-side listener survives full-page navigations, so it captures every WS
  // the page opens across the whole test.
  page.on("websocket", (ws) => {
    ws.on("framereceived", (f) => {
      try {
        const evt = JSON.parse(f.payload as string);
        frames.push({
          url: ws.url(),
          type: evt.type,
          jobStatus: evt.job?.status,
          steps: (evt.steps ?? []).map((s: { name: string; status: string }) => ({
            name: s.name,
            status: s.status,
          })),
        });
      } catch {
        /* ignore non-JSON frames */
      }
    });
  });

  // Instrument window.WebSocket so the test can force-close the live socket.
  // (Playwright's page.on("websocket") observer is read-only — it can't close.)
  // The fake job finishes before the WS connects, so the snapshot already
  // carries the terminal status and the socket stays open by design; we force a
  // disconnect in part C to exercise the connected→disconnected→reconnect path.
  // NB: callbacks run in the browser, so they are plain JS (no TS annotations).
  await page.addInitScript(() => {
    const instances = [];
    const Orig = window.WebSocket;
    class Instrumented extends Orig {
      constructor(...args) {
        super(...args);
        instances.push(this);
      }
    }
    window.WebSocket = Instrumented;
    window.__ws = instances;
  });

  // ---------------------------------------------------------------- A: snapshot
  // Baseline job #1 is already succeeded. The WS must still connect, deliver a
  // snapshot, and the "实时" tag must render (snapshot of a terminal job does
  // NOT auto-close the socket — only a terminal *delta* does).
  await page.goto("/kbs/1/jobs/1");
  await page.waitForLoadState("networkidle");
  await expect(jobHeading(1)).toBeVisible(); // proves SPA booted, not raw JSON
  await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 8_000 });
  await expect.poll(() => frames.some((f) => f.type === "snapshot"), {
    timeout: 8_000,
    message: "first WS frame is a snapshot",
  }).toBe(true);
  await page.screenshot({ path: "../docs/screenshots/realtime-2026-06-28/a-snapshot.png", fullPage: true });

  // -------------------------------------------------- B: server-pushed live delta
  // Trigger a fresh full job on the baseline KB, then open its detail page. The
  // step statuses must advance to "成功" WITHOUT any page.reload() — i.e. driven
  // by WS deltas, not REST polling.
  const trigger = await page.request.post("/kbs/1/jobs", { data: { type: "full" } });
  expect(trigger.status()).toBe(202);
  const newJob = (await trigger.json()).id as number;

  const seenStatuses: string[] = [];
  await page.goto(`/kbs/1/jobs/${newJob}`);
  await page.waitForLoadState("networkidle");
  await expect(jobHeading(newJob)).toBeVisible();

  // Sample the live (WS-driven) job status from the DOM until terminal, with no
  // reload in between. Records the transition sequence as evidence.
  await expect
    .poll(
      async () => {
        const text = await jobHeading(newJob).textContent();
        for (const zh of ["待处理", "运行中", "成功", "失败", "已取消"]) {
          if (text?.includes(zh) && !seenStatuses.includes(zh)) seenStatuses.push(zh);
        }
        return text?.includes("成功");
      },
      { timeout: 60_000, intervals: [200, 500, 1_000], message: "job to reach 成功 via WS" },
    )
    .toBe(true);

  // The server must have pushed at least one delta (step/job status change) —
  // the definitive proof of realtime push vs. client polling.
  const deltas = frames.filter((f) => f.url.endsWith(`/jobs/${newJob}/events`) && f.type === "delta");
  expect(deltas.length, "expected ≥1 WS delta frame for the new job").toBeGreaterThan(0);
  // And the terminal status arrived over the WS (job field on a delta).
  expect(
    frames.some((f) => f.url.endsWith(`/jobs/${newJob}/events`) && f.jobStatus === "succeeded"),
    "expected a WS frame carrying job.status=succeeded",
  ).toBe(true);

  await page.screenshot({ path: "../docs/screenshots/realtime-2026-06-28/b-live-delta.png", fullPage: true });

  // ------------------------------- C: WS drop → "实时" off (polling) → reconnect
  // Force-close the live socket. connected flips false → "实时" hides and the page
  // falls back to the polled job object (useJobPolling retains terminal data), so
  // the data must persist without a reload. Then the hook's 1s reconnect restores
  // the WS and "实时" returns.
  await page.evaluate(() => window.__ws.forEach((w) => w.close()));
  await expect(page.getByText("实时", { exact: true })).toBeHidden({ timeout: 3_000 });
  await expect(jobHeading(newJob)).toBeVisible();
  await expect(jobHeading(newJob)).toContainText("成功");
  await page.screenshot({ path: "../docs/screenshots/realtime-2026-06-28/c-fallback.png", fullPage: true });

  // Reconnect: the hook retries after RECONNECT_MS (1s) → snapshot → "实时" back.
  await expect(page.getByText("实时", { exact: true })).toBeVisible({ timeout: 5_000 });
  await page.screenshot({ path: "../docs/screenshots/realtime-2026-06-28/d-reconnect.png", fullPage: true });

  // eslint-disable-next-line no-console
  console.log(
    "A1_SMOKE_FRAMES=" +
      JSON.stringify({
        totalFrames: frames.length,
        seenDomStatuses: seenStatuses,
        perJob: frames.reduce<Record<string, number>>((acc, f) => {
          const key = f.url.split("/jobs/")[1]?.replace("/events", "") ?? "?";
          acc[key] = (acc[key] ?? 0) + 1;
          return acc;
        }, {}),
        sampleDeltas: deltas.slice(0, 6),
      }),
  );
});
