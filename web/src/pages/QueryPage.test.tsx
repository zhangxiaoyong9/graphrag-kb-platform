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

test("tuning panel is collapsed by default and opens on click", async () => {
  render(
    <KbContext.Provider value={kbCtx}>
      <QueryPage />
    </KbContext.Provider>,
  );
  expect(screen.queryByLabelText("community_level")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  expect(await screen.findByLabelText("community_level")).toBeInTheDocument();
});

test("top_k is hidden for global method", async () => {
  render(
    <KbContext.Provider value={kbCtx}>
      <QueryPage />
    </KbContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  await screen.findByLabelText("community_level");
  fireEvent.click(screen.getByText("global").closest("button")!);
  expect(screen.queryByLabelText("top_k")).not.toBeInTheDocument();
});

test("selecting a preset fills the knobs", async () => {
  server.use(
    http.get("/query-presets", () =>
      HttpResponse.json([
        { id: 9, name: "详尽调研", description: "", method: "global",
          community_level: 1, response_type: "multiple paragraphs",
          temperature: 0.3, is_builtin: true },
      ]),
    ),
  );
  render(
    <KbContext.Provider value={kbCtx}>
      <QueryPage />
    </KbContext.Provider>,
  );
  fireEvent.click(screen.getByRole("button", { name: /调参/ }));
  const select = await screen.findByLabelText("预设");
  fireEvent.change(select, { target: { value: "详尽调研" } });
  await waitFor(() =>
    expect((screen.getByLabelText("community_level") as HTMLInputElement).value).toBe("1"),
  );
});
