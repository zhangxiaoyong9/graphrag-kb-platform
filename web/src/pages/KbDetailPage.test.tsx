import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import KbDetailPage from "./KbDetailPage";

let store = [{ id: 7, status: "succeeded" }];
const server = setupServer(
  http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "demo", method: "standard" })),
  http.get("/kbs/1/documents", () => HttpResponse.json([{ id: 1, title: "doc1", status: "parsed" }])),
  http.get("/kbs/1/jobs", () => HttpResponse.json(store)),
  http.get("/kbs/1/graph", () => HttpResponse.json({ nodes: [], edges: [] })),
  http.get("/kbs/1/cost", () => HttpResponse.json({ total_usd: 0.05, by_step: { workflow_x: 0.04, workflow_y: 0.01 }, by_model: {}, by_job: {} })),
  http.post("/kbs/1/jobs", () => {
    const job = { id: 8, status: "pending" };
    store = [...store, job];
    return HttpResponse.json(job);
  }),
  http.post("/kbs/1/query", async ({ request }) => {
    const body = (await request.json()) as { method?: string; query?: string };
    const method = body.method ?? "local";
    return HttpResponse.json({ answer: `[${method}] fake answer`, method, error: null });
  }),
);
beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  store = [{ id: 7, status: "succeeded" }];
});
afterAll(() => server.close());

test("shows kb, documents, jobs; trigger adds a job", async () => {
  render(<MemoryRouter initialEntries={["/kbs/1"]}><Routes><Route path="/kbs/:id" element={<KbDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("demo")).toBeInTheDocument();
  expect(screen.getByText("doc1")).toBeInTheDocument();
  expect(await screen.findByText("job 7")).toBeInTheDocument();
  await userEvent.click(screen.getByText("Trigger Index"));
  expect(await screen.findByText("job 8")).toBeInTheDocument();
});

test("query box posts method+text and renders answer", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/kbs/1"]}><Routes><Route path="/kbs/:id" element={<KbDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("demo");
  await user.selectOptions(screen.getByLabelText("query method"), "global");
  await user.type(screen.getByPlaceholderText("ask a question"), "what is it?");
  await user.click(screen.getByText("Ask"));
  expect(await screen.findByText("[global] fake answer")).toBeInTheDocument();
});
