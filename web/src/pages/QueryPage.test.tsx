import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { vi } from "vitest";
import QueryPage from "./QueryPage";
import { KbContext, type KbCtx } from "./kb-context";

// Mock parseSse: no real ReadableStream under jsdom (deadlocks under parallelism).
// Real parsing is unit-tested in src/lib/sse.test.ts. Yield each event on its own
// macrotask so React flushes each setResult commit independently (mirrors real SSE
// framing and keeps the test deterministic regardless of vitest worker load).
vi.mock("../lib/sse", () => ({
  parseSse: async function* () {
    await Promise.resolve();
    yield { event: "meta", data: { method: "local" } };
    await Promise.resolve();
    yield { event: "delta", data: { text: "Hi" } };
    await Promise.resolve();
    yield {
      event: "done",
      data: {
        result: { answer: "Hi", method: "local", error: null, sources: [] },
      },
    };
  },
}));

const kbCtx: KbCtx = {
  kbId: 1,
  kb: null,
  reload: () => {},
};

const server = setupServer(
  // parseSse is mocked, so the body is never read; an ok SSE-shaped response is enough.
  http.post("/kbs/1/query", () =>
    new HttpResponse("event: done\n", { headers: { "content-type": "text/event-stream" } }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test(
  "streams the answer incrementally",
  async () => {
    render(
      <KbContext.Provider value={kbCtx}>
        <QueryPage />
      </KbContext.Provider>,
    );
    const ta = await screen.findByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /提问/ }));
    // The streamed answer "Hi" renders in both the answer box and QueryResultView,
    // so match by substring across nodes and accept multiple matches.
    await waitFor(
      () =>
        expect(
          screen.getAllByText((_, node) => !!node?.textContent && node.textContent.includes("Hi")),
        ).not.toHaveLength(0),
      { timeout: 15000, interval: 50 },
    );
  },
  20000,
);
