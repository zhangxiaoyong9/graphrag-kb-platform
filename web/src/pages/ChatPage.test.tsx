import { render, screen, fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import ChatPage from "./ChatPage";

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
    // The `delta` event renders "A:hello" into the optimistic assistant bubble
    // before `done` replaces it with the persisted message.
    //
    // NOTE on the poll loop: the answer streams over SSE, and ChatPage consumes
    // `response.body.getReader()` inside `send()`'s async loop. In jsdom, React
    // defers the commit of those streamed state updates, and testing-library's
    // waitFor/findByText polling can starve both that commit and the stream's
    // microtask reads; polling with real macrotask waits (`setTimeout`) lets
    // React flush and the stream drain. Under full-suite parallel jsdom load the
    // stream drain can take several seconds, hence the generous window + outer
    // timeout. In a real browser the deltas render within milliseconds.
    const start = Date.now();
    while (Date.now() - start < 20000 && !screen.queryByText("A:hello")) {
      await new Promise((r) => setTimeout(r, 50));
    }
    expect(screen.getByText("A:hello")).toBeInTheDocument();
  },
  40000,
);
