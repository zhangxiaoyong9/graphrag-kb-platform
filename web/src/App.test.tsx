import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import App from "./App";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([])),
  http.get("/health", () =>
    HttpResponse.json({ status: "ok", db: "ok", worker: { last_heartbeat_at: null, stale: false } }),
  ),
  http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "kb1", method: "standard", settings: {} })),
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 0,
      text: "Alpha body",
      citations: [],
    }),
  ),
  http.get("/kbs/1/graph", () => HttpResponse.json({ nodes: [], edges: [] })),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("renders the dashboard at /", async () => {
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  // Sidebar brand is always present
  expect(screen.getByText("知识库平台")).toBeInTheDocument();
  // Dashboard hero headline
  expect(await screen.findByText(/从非结构化文本到可检索的知识图谱/)).toBeInTheDocument();
});

test("renders document detail route", async () => {
  render(
    <MemoryRouter initialEntries={["/kbs/1/documents/7"]}>
      <App />
    </MemoryRouter>,
  );
  expect(await screen.findByText("alpha.md")).toBeInTheDocument();
  expect(screen.getByText(/Alpha body/)).toBeInTheDocument();
});
