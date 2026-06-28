import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createKb, deleteDocument, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit } from "./client";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.post("/kbs", async ({ request }) => HttpResponse.json({ id: 2, name: (await request.json() as { name: string }).name, method: "standard" })),
  http.post("/units/5/retry", () => HttpResponse.json({ ok: true })),
  http.get("/kbs/1/documents/7", () =>
    HttpResponse.json({
      id: 7,
      title: "alpha.md",
      status: "parsed",
      bytes: 100,
      chunk_count: 1,
      text: "Alpha body",
      citations: [{ id: "chunk:c1", label: "分块 1", snippet: "Alpha body", chunk_id: "c1", ordinal: 0 }],
    }),
  ),
  http.get("/kbs/1/documents/7/citations/chunk%3Ac1/evidence", () =>
    HttpResponse.json({
      citation_id: "chunk:c1",
      matched: "Alpha body",
      before: null,
      after: "Beta context",
      source: { document_id: 7, document_title: "alpha.md", chunk_id: "c1", ordinal: 0 },
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("listKbs + createKb + retryUnit", async () => {
  const kbs = await listKbs();
  expect(kbs[0].name).toBe("kb1");
  const kb = await createKb({ name: "kb2", llm_profile_id: 1 });
  expect(kb.id).toBe(2);
  expect((await retryUnit(5)).ok).toBe(true);
});

test("document detail client returns text and citations", async () => {
  const detail = await getDocumentDetail(1, 7);
  expect(detail.title).toBe("alpha.md");
  expect(detail.text).toBe("Alpha body");
  expect(detail.citations[0]).toMatchObject({ id: "chunk:c1", label: "分块 1" });
});

test("document evidence client encodes citation ids", async () => {
  const evidence = await getDocumentEvidence(1, 7, "chunk:c1");
  expect(evidence.matched).toBe("Alpha body");
  expect(evidence.after).toBe("Beta context");
  expect(evidence.source.chunk_id).toBe("c1");
});

test("deleteDocument returns shrinkJobCreated + jobId on 202", async () => {
  server.use(
    http.delete("/kbs/1/documents/9", () =>
      HttpResponse.json({ id: 42, status: "pending" }, { status: 202 }),
    ),
  );
  expect(await deleteDocument(1, 9)).toEqual({ shrinkJobCreated: true, jobId: 42 });
});

test("deleteDocument returns shrinkJobCreated false on 204", async () => {
  server.use(
    http.delete("/kbs/1/documents/9", () => new HttpResponse(null, { status: 204 })),
  );
  expect(await deleteDocument(1, 9)).toEqual({ shrinkJobCreated: false });
});
