import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import KbOverviewPage from "./KbOverviewPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard", settings: {}, data_root: "/data/kb-1", llm_profile: null, embedding_profile: null };

const server = setupServer(
  http.get("/kbs/1/documents", () => HttpResponse.json([])),
  http.get("/kbs/1/jobs", () => HttpResponse.json([])),
  http.get("/kbs/1/cost", () => HttpResponse.json({ total_usd: 0, by_step: {}, by_model: {}, by_job: {} })),
  http.get("/kbs/1/stats", () =>
    HttpResponse.json({
      updated_at: "2026-06-28T00:00:00+00:00",
      document_count: 2, chunk_count: 5,
      entity_count: 9, relationship_count: 7,
      community_count: 3, community_report_count: 4, text_unit_count: 5,
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/kbs/1"]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id" element={<KbOverviewPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders graph-scale card with stats counts", async () => {
  renderPage();
  expect(await screen.findByText("图谱规模")).toBeInTheDocument();
  expect(screen.getByText("9")).toBeInTheDocument();       // entity_count
  expect(screen.getByText("7")).toBeInTheDocument();       // relationship_count
  expect(screen.getByText("3")).toBeInTheDocument();       // community_count
});

test("shows dash placeholders when stats empty", async () => {
  server.use(http.get("/kbs/1/stats", () => HttpResponse.json({})));
  renderPage();
  await waitFor(() => expect(screen.getByText("图谱规模")).toBeInTheDocument());
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
});
