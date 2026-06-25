import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { listKbs, createKb, retryUnit } from "./client";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.post("/kbs", async ({ request }) => HttpResponse.json({ id: 2, name: (await request.json() as { name: string }).name, method: "standard" })),
  http.post("/units/5/retry", () => HttpResponse.json({ ok: true })),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("listKbs + createKb + retryUnit", async () => {
  const kbs = await listKbs();
  expect(kbs[0].name).toBe("kb1");
  const kb = await createKb({ name: "kb2" });
  expect(kb.id).toBe(2);
  expect((await retryUnit(5)).ok).toBe(true);
});
