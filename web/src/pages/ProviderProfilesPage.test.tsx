import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import { ProviderProfilesPage } from "./ProviderProfilesPage";

const profiles = [
  { id: 1, name: "DeepSeek", kind: "llm", provider: "deepseek", model: "deepseek-chat",
    api_base: null, api_version: null, structured_output: false, api_keys_count: 2, ssl_verify: true },
];
let nextId = 2;

const server = setupServer(
  http.get("/provider-profiles", () => HttpResponse.json(profiles)),
  http.post("/provider-profiles", async ({ request }) => {
    const b = (await request.json()) as { name: string; provider: string; model: string; api_keys: string[]; ssl_verify?: boolean };
    const created = { id: nextId++, name: b.name, kind: "llm" as const, provider: b.provider,
      model: b.model, api_base: null, api_version: null, structured_output: true,
      api_keys_count: b.api_keys.filter((k) => k).length, ssl_verify: b.ssl_verify ?? true };
    profiles.push(created);
    return HttpResponse.json(created);
  }),
  http.delete("/provider-profiles/1", () => new HttpResponse(null, { status: 409 })),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists profiles and adds one with a key", async () => {
  render(<MemoryRouter><ProviderProfilesPage /></MemoryRouter>);
  expect(await screen.findByText("DeepSeek")).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("名称，如 DeepSeek"), { target: { value: "OpenAI" } });
  fireEvent.change(screen.getByPlaceholderText("provider"), { target: { value: "openai" } });
  fireEvent.change(screen.getByPlaceholderText("deepseek-chat"), { target: { value: "gpt-4o-mini" } });
  fireEvent.change(screen.getByPlaceholderText("sk-..."), { target: { value: "sk-xxx" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));
  await waitFor(() => expect(screen.getByText("OpenAI")).toBeInTheDocument());
});

test("delete conflict surfaces an error", async () => {
  render(<MemoryRouter><ProviderProfilesPage /></MemoryRouter>);
  await screen.findByText("DeepSeek");
  fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
  await waitFor(() => expect(screen.getByText(/被知识库引用|409|删除失败/)).toBeInTheDocument());
});

test("create sends ssl_verify from the checkbox", async () => {
  let captured: any = null;
  server.use(
    http.post("/provider-profiles", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json({ id: 99, ...captured, api_keys_count: 1, ssl_verify: captured.ssl_verify });
    }),
  );
  render(<MemoryRouter><ProviderProfilesPage /></MemoryRouter>);
  await screen.findByText("DeepSeek");
  fireEvent.change(screen.getByPlaceholderText("名称，如 DeepSeek"), { target: { value: "OllamaSelf" } });
  fireEvent.change(screen.getByPlaceholderText("provider"), { target: { value: "ollama" } });
  fireEvent.change(screen.getByPlaceholderText("deepseek-chat"), { target: { value: "nomic-embed-text" } });
  fireEvent.change(screen.getByPlaceholderText("sk-..."), { target: { value: "ollama" } });
  fireEvent.click(screen.getByLabelText(/校验 SSL/));  // uncheck -> ssl_verify false
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));
  await waitFor(() => expect(captured).not.toBeNull());
  expect(captured.ssl_verify).toBe(false);
});
