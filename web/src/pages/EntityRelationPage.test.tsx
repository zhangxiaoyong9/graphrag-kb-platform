import { fireEvent, render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import EntityRelationPage from "./EntityRelationPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard", settings: {}, llm_profile: null, embedding_profile: null };

const server = setupServer(
  http.get("/kbs/1/graph", () =>
    HttpResponse.json({
      nodes: [
        { id: "Alpha", title: "Alpha", type: "CONCEPT", degree: 2, community: "10" },
        { id: "Beta", title: "Beta", type: "PERSON", degree: 1, community: "10" },
        { id: "Gamma", title: "Gamma", type: "PLACE", degree: 0, community: "20" },
      ],
      edges: [
        { source: "Alpha", target: "Beta", weight: 2, description: "Alpha relates to Beta" },
      ],
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/kbs/1/documents/7/entities"]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id/documents/:docId/entities" element={<EntityRelationPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders entity and relationship lists", async () => {
  renderPage();
  // gate on a loaded-only element (entity buttons appear only after the graph loads;
  // the "实体 / 关系" title also shows in the loading skeleton, so it can't gate).
  expect(await screen.findByRole("button", { name: /查看实体 Alpha 的关系/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /查看实体 Beta 的关系/ })).toBeInTheDocument();
  expect(screen.getByText("实体 / 关系")).toBeInTheDocument();
  expect(screen.getByText("Alpha relates to Beta")).toBeInTheDocument();
});

test("clicking an entity filters related relationships", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "查看实体 Alpha 的关系" }));
  expect(screen.getByText("已选择实体：Alpha")).toBeInTheDocument();
  expect(screen.getByText("Alpha relates to Beta")).toBeInTheDocument();
});

test("clicking a relationship selects its connected entity", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "查看关系 Alpha 到 Beta" }));
  expect(screen.getByText("已选择实体：Alpha")).toBeInTheDocument();
});

test("renders empty state when graph has no data", async () => {
  server.use(http.get("/kbs/1/graph", () => HttpResponse.json({ nodes: [], edges: [] })));
  renderPage();
  expect(await screen.findByText("暂无实体或关系")).toBeInTheDocument();
});

test("renders local error when graph loading fails", async () => {
  server.use(http.get("/kbs/1/graph", () => new HttpResponse(null, { status: 500 })));
  renderPage();
  expect(await screen.findByText("实体关系加载失败")).toBeInTheDocument();
});
