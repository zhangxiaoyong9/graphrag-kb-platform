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
  http.post("/conversations/8/messages", async ({ request }) => {
    const b = (await request.json()) as { content: string };
    return HttpResponse.json({
      id: 11,
      role: "assistant",
      content: `A:${b.content}`,
      method: "local",
      rewritten_query: null,
      rewrite_fell_back: false,
      sources: [],
    });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("creates a conversation and shows the answer", async () => {
  render(
    <MemoryRouter>
      <ChatPage />
    </MemoryRouter>,
  );
  // KB list renders; create a new conversation
  const newBtn = await screen.findByRole("button", { name: /新建/ });
  fireEvent.click(newBtn);
  // type and send
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /发送/ }));
  expect(await screen.findByText("A:hello")).toBeInTheDocument();
});
