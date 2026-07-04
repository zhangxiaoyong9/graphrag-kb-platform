import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import DocumentDetailPage from "./DocumentDetailPage";
import { KbContext } from "./kb-context";

const kb = { id: 1, name: "kb1", method: "standard", settings: {}, data_root: "/data/kb-1", llm_profile: null, embedding_profile: null };

const server = setupServer(
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 2,
      text: "Alpha body\n\nBeta body",
      citations: [
        { id: "chunk:c1", label: "分块 1", snippet: "Alpha body", chunk_id: "c1", ordinal: 0 },
        { id: "chunk:c2", label: "分块 2", snippet: "Beta body", chunk_id: "c2", ordinal: 1 },
      ],
    }),
  ),
  http.get("/kbs/1/documents/8", () =>
    HttpResponse.json({
      id: 8,
      title: "empty.md",
      status: "parsed",
      bytes: 11,
      chunk_count: 0,
      text: "Hello world",
      citations: [],
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c1",
      matched: "Alpha body",
      before: null,
      after: "Beta body",
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c1", ordinal: 0 },
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac2/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c2",
      matched: "Beta body",
      before: "Alpha body",
      after: null,
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c2", ordinal: 1 },
    }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderPage(path = "/kbs/1/documents/7") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <KbContext.Provider value={{ kbId: 1, kb, reload: () => undefined }}>
        <Routes>
          <Route path="/kbs/:id/documents/:docId" element={<DocumentDetailPage />} />
        </Routes>
      </KbContext.Provider>
    </MemoryRouter>,
  );
}

test("renders document title, body, and citations", async () => {
  renderPage();
  expect(await screen.findByText("alpha.md")).toBeInTheDocument();
  // body spans both paragraphs; citation snippets are single-segment, so this
  // uniquely matches the document body, not a citation snippet.
  expect(screen.getByText(/Alpha body\s+Beta body/)).toBeInTheDocument();
  expect(screen.getByText("分块 1")).toBeInTheDocument();
  expect(screen.getByText("分块 2")).toBeInTheDocument();
});

test("opens evidence drawer and replaces content when another citation is clicked", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("Alpha body")).toBeInTheDocument();
  expect(screen.getByText("后文")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /查看证据 分块 2/ }));
  await waitFor(() => expect(screen.getByText("Beta body")).toBeInTheDocument());
  expect(screen.getByText("前文")).toBeInTheDocument();
});

test("closes evidence drawer without removing document body", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("证据详情")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "关闭证据抽屉" }));
  expect(screen.queryByText("证据详情")).not.toBeInTheDocument();
  expect(screen.getByText(/Alpha body\s+Beta body/)).toBeInTheDocument();
});

test("shows empty citation state", async () => {
  renderPage("/kbs/1/documents/8");
  expect(await screen.findByText("empty.md")).toBeInTheDocument();
  expect(screen.getByText("暂无可验证引用")).toBeInTheDocument();
});

test("evidence load failure stays local to drawer", async () => {
  server.use(
    http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () => new HttpResponse(null, { status: 500 })),
  );
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: /查看证据 分块 1/ }));
  expect(await screen.findByText("证据加载失败")).toBeInTheDocument();
  expect(screen.getByText(/Alpha body\s+Beta body/)).toBeInTheDocument();
});
