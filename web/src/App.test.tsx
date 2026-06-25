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
