import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import ChatPage from "./ChatPage";

// Mock parseSse so no real ReadableStream/getReader runs under jsdom (which
// deadlocks under vitest worker parallelism). Real parsing is unit-tested in
// sse.test.ts and end-to-end via Playwright.
vi.mock("../lib/sse", () => ({
  // Yield each event on its own macrotask so React flushes each setMessages
  // commit independently (mirrors real SSE framing). Without the inter-yield
  // waits, the three events fire in one synchronous burst and, under vitest
  // worker parallelism, React can defer/lose the commit of the delta — making
  // the test flaky. The waits make each render deterministic regardless of load.
  parseSse: async function* () {
    await Promise.resolve();
    yield { event: "meta", data: { method: "local", rewrite_fell_back: false } };
    await Promise.resolve();
    yield { event: "delta", data: { text: "A:hello" } };
    await Promise.resolve();
    yield {
      event: "done",
      data: {
        message: {
          id: 11, role: "assistant", content: "A:hello", method: "local",
          rewritten_query: null, rewrite_fell_back: false, sources: [],
        },
      },
    };
  },
}));

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([{ id: 1, name: "kb1", method: "standard" }])),
  http.get("/kbs/1/conversations", () => HttpResponse.json([])),
  http.post("/kbs/1/conversations", () =>
    HttpResponse.json({ id: 8, kb_id: 1, title: "", updated_at: null, snippet: "" }),
  ),
  http.get("/conversations/8", () => HttpResponse.json({ id: 8, kb_id: 1, title: "", messages: [] })),
  http.post("/conversations/8/messages", () => {
    const body =
      'event: meta\ndata: {"method":"local","rewrite_fell_back":false}\n\n' +
      'event: delta\ndata: {"text":"A:hello"}\n\n' +
      'event: done\ndata: {"message":{"id":11,"role":"assistant","content":"A:hello","method":"local","rewrite_fell_back":false,"sources":[]}}\n\n';
    return new HttpResponse(body, { headers: { "content-type": "text/event-stream" } });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test(
  "creates a conversation and streams the answer over SSE",
  async () => {
    render(
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>,
    );
    // KB list renders; create a new conversation
    const newBtn = await screen.findByRole("button", { name: /新建/ });
    fireEvent.click(newBtn);
    // Wait for the conversation to be selected (textarea enables once convId set).
    const ta = await screen.findByRole("textbox");
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));
    // parseSse is mocked (no real ReadableStream/getReader under jsdom, which
    // deadlocks under vitest worker parallelism). The streamed answer "A:hello"
    // is rendered in both the assistant bubble and QueryResultView, so we match
    // by substring and accept multiple nodes. waitFor with a generous timeout
    // absorbs the React-commit scheduling jitter under parallel jsdom load.
    // Real parseSse parsing is unit-tested in sse.test.ts.
    await waitFor(
      () =>
        expect(
          screen.getAllByText((_, node) => !!node?.textContent && node.textContent.includes("A:hello")),
        ).not.toHaveLength(0),
      { timeout: 15000, interval: 50 },
    );
  },
  20000,
);
