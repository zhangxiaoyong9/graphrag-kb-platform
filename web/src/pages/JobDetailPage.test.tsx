import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import JobDetailPage from "./JobDetailPage";

const server = setupServer(
  http.get("/jobs/9", () => HttpResponse.json({ id: 9, status: "partially_failed", steps: [{ id: 91, name: "extract_graph", ordinal: 2, kind: "unit_fanout", status: "partially_failed", progress: { pending: 0, running: 0, succeeded: 1, failed: 1, total: 2 } }] })),
  http.get("/steps/91/units", () => HttpResponse.json([{ id: 911, subject_id: "chunk-fail", status: "failed", error: "boom", llm_raw_output: null, needs_reconsolidation: false }])),
  http.post("/units/911/retry", () => HttpResponse.json({ ok: true })),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("shows steps, units, retry failed unit", async () => {
  render(<MemoryRouter initialEntries={["/kbs/1/jobs/9"]}><Routes><Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("extract_graph")).toBeInTheDocument();
  await userEvent.click(screen.getByText("extract_graph"));
  expect(await screen.findByText("chunk-fail".slice(0, 12))).toBeInTheDocument();
  await userEvent.click(screen.getByText("retry"));
});
