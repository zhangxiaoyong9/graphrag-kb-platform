import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import JobDetailPage from "./JobDetailPage";

const server = setupServer(
  http.get("/jobs/9", () => HttpResponse.json({ id: 9, status: "partially_failed", steps: [{ id: 91, name: "extract_graph", ordinal: 2, kind: "unit_fanout", status: "partially_failed", progress: { pending: 0, running: 0, succeeded: 1, failed: 1, total: 2 } }] })),
  http.get("/kbs/1/jobs/9/cost", () => HttpResponse.json({ total_usd: 0.0123, by_step: { workflow_a: 0.01, workflow_b: 0.0023 }, by_model: {} })),
  http.get("/steps/91/units", () => HttpResponse.json({ items: [{ id: 911, subject_id: "chunk-fail", status: "failed", error: "boom", llm_raw_output: null, needs_reconsolidation: false }], total: 1 })),
  http.post("/units/911/retry", () => HttpResponse.json({ ok: true })),
  http.post("/steps/91/retry", () => HttpResponse.json({ reset: 1 })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

function renderJob() {
  return render(<MemoryRouter initialEntries={["/kbs/1/jobs/9"]}><Routes><Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter>);
}

test("shows steps, units, retry failed unit", async () => {
  renderJob();
  expect(await screen.findByText("extract_graph")).toBeInTheDocument();
  await userEvent.click(screen.getByText("extract_graph"));
  expect(await screen.findByText("chunk-fail".slice(0, 12))).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "重试" }));
});

test("step-level retry button appears and calls retryStep", async () => {
  renderJob();
  await screen.findByText("extract_graph");
  await userEvent.click(screen.getByText("extract_graph"));
  const btn = await screen.findByRole("button", { name: "重试失败 unit" });
  expect(btn).toBeInTheDocument();
  await userEvent.click(btn);
});

test("failed atomic step shows a retry-step button (no units to retry)", async () => {
  // generate_text_embeddings has no units; when it fails (e.g. embed endpoint
  // down) the step lands in `failed` and must still be retryable, with a label
  // that doesn't promise per-unit retry.
  server.use(
    http.get("/jobs/10", () => HttpResponse.json({ id: 10, status: "failed", steps: [{ id: 101, name: "generate_text_embeddings", ordinal: 7, kind: "atomic", status: "failed", progress: null }] })),
    http.get("/kbs/1/jobs/10/cost", () => HttpResponse.json({ total_usd: 0, by_step: {}, by_model: {} })),
    http.get("/steps/101/units", () => HttpResponse.json({ items: [], total: 0 })),
    http.post("/steps/101/retry", () => HttpResponse.json({ reset: 0 })),
  );
  render(<MemoryRouter initialEntries={["/kbs/1/jobs/10"]}><Routes><Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("generate_text_embeddings");
  await userEvent.click(screen.getByText("generate_text_embeddings"));
  const btn = await screen.findByRole("button", { name: "重试步骤" });
  expect(btn).toBeInTheDocument();
  await userEvent.click(btn);
});
