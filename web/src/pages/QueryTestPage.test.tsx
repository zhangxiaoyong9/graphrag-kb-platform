import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import QueryTestPage from "./QueryTestPage";

// Mock parseSse: no real ReadableStream under jsdom (deadlocks under parallelism).
// Real parsing is unit-tested in src/lib/sse.test.ts. Yield each event on its own
// macrotask so React flushes each setResult commit independently (mirrors real SSE
// framing and keeps the test deterministic regardless of vitest worker load).
//
// The factory reads the module-level `doneResult` variable so individual tests can
// override the done payload (e.g. to surface truncated:true) without inventing a new
// mock mechanism — they reuse this same generator + KB-loading pattern verbatim.
const doneResult: any = {
  answer: "Hello",
  method: "local",
  error: null,
  sources: [],
};
vi.mock("../lib/sse", () => ({
  parseSse: async function* () {
    await Promise.resolve();
    yield { event: "meta", data: { method: "local" } };
    await Promise.resolve();
    yield { event: "delta", data: { text: "Hel" } };
    await Promise.resolve();
    yield { event: "delta", data: { text: "lo" } };
    await Promise.resolve();
    yield {
      event: "done",
      data: {
        result: { ...doneResult },
      },
    };
  },
}));

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
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
      <MemoryRouter>
        <QueryTestPage />
      </MemoryRouter>,
    );
    const ta = await screen.findByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /提问/ }));
    // The streamed answer "Hello" renders in both the answer box and QueryResultView,
    // so match by substring across nodes and accept multiple matches.
    await waitFor(
      () =>
        expect(
          screen.getAllByText((_, node) => !!node?.textContent && node.textContent.includes("Hello")),
        ).not.toHaveLength(0),
      { timeout: 15000, interval: 50 },
    );
  },
  20000,
);

test(
  "shows the truncated notice when the done result is truncated",
  async () => {
    // Override only the done-payload result object: truncated:true now flows through
    // the existing parseSse mock + KB-loading path verbatim — no new mock mechanism.
    doneResult.truncated = true;
    render(
      <MemoryRouter>
        <QueryTestPage />
      </MemoryRouter>,
    );
    const ta = await screen.findByRole("textbox");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.click(screen.getByRole("button", { name: /提问/ }));
    expect(await screen.findByText(/结果已达行数上限/, undefined, { timeout: 15000 })).toBeInTheDocument();
    doneResult.truncated = false;
  },
  20000,
);
