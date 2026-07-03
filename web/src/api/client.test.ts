import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createKb, deleteConversation, deleteDocument, getDocumentDetail, getDocumentEvidence, listKbs, retryUnit, createConversation, sendMessage, query, getLlmHealth } from "./client";

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
  http.post("/kbs/1/conversations", () =>
    HttpResponse.json({ id: 9, kb_id: 1, title: "", updated_at: null, snippet: "" }),
  ),
  http.post("/conversations/9/messages", async ({ request }) => {
    const b = (await request.json()) as { content: string; method: string };
    const body =
      `event: meta\ndata: {"method":"${b.method}","rewrite_fell_back":false}\n\n` +
      `event: delta\ndata: {"text":"A:${b.content}"}\n\n` +
      `event: done\ndata: {"message":{"id":10,"role":"assistant","content":"A:${b.content}","method":"${b.method}","rewrite_fell_back":false,"sources":[]}}\n\n`;
    return new HttpResponse(body, { headers: { "content-type": "text/event-stream" } });
  }),
  http.get("/llm/health", () =>
    HttpResponse.json({
      profiles: [
        { provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" },
        { provider: "deepseek", model: "deepseek-chat", api_base: "https://api.deepseek.com", state: "open" },
      ],
      metrics: { ttft_ms_p50: 123.4, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 2, successes: 40 },
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

test("deleteConversation resolves on a 204 empty body", async () => {
  server.use(
    http.delete("/conversations/3", () => new HttpResponse(null, { status: 204 })),
  );
  await expect(deleteConversation(3)).resolves.toBeUndefined();
});

test("conversation client posts to the right paths", async () => {
  const c = await createConversation(1);
  expect(c.id).toBe(9);
  // sendMessage returns the raw SSE Response; the stream body is never read
  // here (real parsing is unit-tested in sse.test.ts). Only the response
  // shape is asserted — no getReader()/parseSse under jsdom.
  const resp = await sendMessage(9, "hi", "local");
  expect(resp.ok).toBe(true);
  expect(resp.headers.get("content-type")).toContain("text/event-stream");
});

test("query sends params in body when provided", async () => {
  let captured: unknown;
  server.use(
    http.post("/kbs/1/query", async ({ request }) => {
      captured = await request.json();
      return new HttpResponse("event: done\n", { headers: { "content-type": "text/event-stream" } });
    }),
  );
  await query(1, "local", "q", { community_level: 1 });
  expect((captured as { params: unknown }).params).toEqual({ community_level: 1 });
});

test("query omits params when undefined", async () => {
  let captured: unknown;
  server.use(
    http.post("/kbs/1/query", async ({ request }) => {
      captured = await request.json();
      return new HttpResponse("event: done\n", { headers: { "content-type": "text/event-stream" } });
    }),
  );
  await query(1, "local", "q");
  expect((captured as { params?: unknown }).params).toBeUndefined();
});

test("getLlmHealth returns profiles + metrics", async () => {
  const out = await getLlmHealth();
  expect(out.profiles).toHaveLength(2);
  expect(out.profiles[0]).toEqual({ provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" });
  expect(out.metrics.failovers).toBe(2);
  expect(out.metrics.ttft_ms_p50).toBe(123.4);
  expect(out.metrics.failover_detect_ms_p50).toBeNull();
});
