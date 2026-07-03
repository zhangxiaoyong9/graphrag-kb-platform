import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import QueryPresetsPage from "./QueryPresetsPage";

const BUILTIN = { id: 1, name: "默认", description: "", method: "local", is_builtin: true };
const CUSTOM = {
  id: 7,
  name: "我的预设",
  description: "desc",
  method: "global",
  community_level: 1,
  response_type: "multiple paragraphs",
  top_k: null,
  temperature: 0.3,
  system_prompt: null,
  is_builtin: false,
};

const server = setupServer(http.get("/query-presets", () => HttpResponse.json([BUILTIN])));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists built-in presets with no edit/delete control", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByText("默认")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /编辑|删除/ })).not.toBeInTheDocument(),
  );
});

test("renders the create form", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByPlaceholderText("名称")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /新建/ })).toBeInTheDocument();
});

test("editing a custom preset loads it into the form", async () => {
  server.use(http.get("/query-presets", () => HttpResponse.json([BUILTIN, CUSTOM])));
  render(<QueryPresetsPage />);
  await screen.findByText("我的预设");
  fireEvent.click(screen.getByRole("button", { name: /^编辑 我的预设$/ }));
  expect((screen.getByPlaceholderText("名称") as HTMLInputElement).value).toBe("我的预设");
  expect((screen.getByPlaceholderText("描述(可空)") as HTMLInputElement).value).toBe("desc");
  expect((screen.getByDisplayValue("global") as HTMLSelectElement).value).toBe("global");
  expect(screen.getByRole("button", { name: /保存修改/ })).toBeInTheDocument();
});

test("create failure surfaces an error message", async () => {
  server.use(http.post("/query-presets", () => new HttpResponse(null, { status: 500 })));
  render(<QueryPresetsPage />);
  await screen.findByPlaceholderText("名称");
  fireEvent.change(screen.getByPlaceholderText("名称"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.getByRole("alert").textContent).toMatch(/保存失败/);
});

test("saving a hybrid preset sends hops", async () => {
  const captured: any[] = [];
  server.use(
    http.get("/query-presets", () => HttpResponse.json([BUILTIN])),
    http.post("/query-presets", async ({ request }) => {
      captured.push(await request.json());
      return HttpResponse.json({ id: 9, is_builtin: false, ...(captured[0]) });
    }),
  );
  render(<QueryPresetsPage />);
  await screen.findByPlaceholderText("名称");
  fireEvent.change(screen.getByPlaceholderText("名称"), { target: { value: "hyb" } });
  // select hybrid method so the hops input renders
  fireEvent.change(screen.getByDisplayValue("local"), { target: { value: "hybrid" } });
  fireEvent.change(screen.getByPlaceholderText("hops(可空,hybrid)"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  await waitFor(() => expect(captured.length).toBe(1));
  expect(captured[0].method).toBe("hybrid");
  expect(captured[0].hops).toBe(3);
});
