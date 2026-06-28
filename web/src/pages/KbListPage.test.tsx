import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import KbListPage from "./KbListPage";

let store = [{ id: 1, name: "demo", method: "standard" }];
const server = setupServer(
  http.get("/kbs", () => HttpResponse.json(store)),
  http.get("/provider-profiles", ({ request }) => {
    const kind = new URL(request.url).searchParams.get("kind");
    if (kind === "embedding") return HttpResponse.json([]);
    return HttpResponse.json([
      { id: 1, name: "DS", kind: "llm", provider: "deepseek", model: "deepseek-chat", api_base: null, api_version: null, structured_output: false, api_keys_count: 1 },
    ]);
  }),
  http.post("/kbs", async ({ request }) => {
    const body = (await request.json()) as { name: string };
    const kb = { id: 2, name: body.name, method: "standard" };
    store = [...store, kb];
    return HttpResponse.json(kb);
  }),
);
beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  store = [{ id: 1, name: "demo", method: "standard" }];
});
afterAll(() => server.close());

test("lists kbs and creates one", async () => {
  render(
    <MemoryRouter>
      <KbListPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText("demo")).toBeInTheDocument();
  // an LLM profile is required before the create button submits
  await userEvent.selectOptions(await screen.findByLabelText(/LLM 配置/), "1");
  await userEvent.type(screen.getByPlaceholderText("请输入知识库名称"), "newkb");
  await userEvent.click(screen.getByRole("button", { name: "创建知识库" }));
  expect(await screen.findByText("newkb")).toBeInTheDocument();
});
