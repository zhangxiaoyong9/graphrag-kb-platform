import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { vi } from "vitest";
import KbForm from "./KbForm";

const captured: { url: string; body: unknown }[] = [];
const server = setupServer(
  http.post("/kbs", async ({ request }) => {
    const body = (await request.json()) as { settings_yaml: string };
    captured.push({ url: request.url, body });
    return HttpResponse.json({
      id: 1,
      name: "x",
      method: "standard",
      settings: JSON.parse(body.settings_yaml || "{}"),
    });
  }),
  http.get("/prompts/defaults", () =>
    HttpResponse.json({
      extract_graph: "DEFAULT_EXTRACT",
      summarize_descriptions: "DEFAULT_SUMMARIZE",
      community_reports: "DEFAULT_REPORT",
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  captured.length = 0;
});
afterAll(() => server.close());

function renderForm(onCreated = vi.fn()) {
  render(
    <MemoryRouter>
      <KbForm onCreated={onCreated} />
    </MemoryRouter>,
  );
  return onCreated;
}

test("form has all required sections", () => {
  renderForm();
  for (const label of [
    "LLM 模型",
    "Embedding 模型",
    "分块 Chunking",
    "图谱抽取 Extract Graph",
    "描述摘要 Summarize",
    "社区报告 Community Reports",
    "聚类 Clustering",
  ]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("prompts section has 3 textareas and view-default toggle shows fetched default", async () => {
  renderForm();
  expect(
    screen.getByText("提示词 Prompts（留空=用 graphrag 默认）"),
  ).toBeInTheDocument();
  // 3 view-default buttons (one per prompt)
  const viewButtons = screen.getAllByRole("button", {
    name: /查看 graphrag 默认/,
  });
  expect(viewButtons).toHaveLength(3);
  // click the first one (extract_graph) and confirm the fetched default renders
  await userEvent.click(viewButtons[0]);
  await waitFor(() =>
    expect(screen.getByText("DEFAULT_EXTRACT")).toBeInTheDocument(),
  );
});

test("submits buildSettings output as settings_yaml", async () => {
  const onCreated = renderForm();
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "my-kb");
  await userEvent.type(screen.getByPlaceholderText(/^deepseek$/), "deepseek");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]
    ?.body as { settings_yaml: string; name: string };
  expect(last.name).toBe("my-kb");
  const parsed = JSON.parse(last.settings_yaml);
  expect(parsed.llm.model_provider).toBe("deepseek");
});

test("advanced override replaces form-built settings when non-empty", async () => {
  const onCreated = renderForm();
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "ovr-kb");
  // open advanced panel
  await userEvent.click(screen.getByRole("button", { name: /高级/ }));
  const ta = screen.getByLabelText(/原始 settings_yaml/);
  fireEvent.change(ta, {
    target: { value: '{"llm":{"model_provider":"openai","model":"gpt-4o"}}' },
  });
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]
    ?.body as { settings_yaml: string };
  const parsed = JSON.parse(last.settings_yaml);
  expect(parsed.llm.model_provider).toBe("openai");
  expect(parsed.llm.model).toBe("gpt-4o");
});

test("bad JSON in advanced override shows inline error and does not submit", async () => {
  const onCreated = renderForm();
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "bad-kb");
  await userEvent.click(screen.getByRole("button", { name: /高级/ }));
  const ta = screen.getByLabelText(/原始 settings_yaml/);
  fireEvent.change(ta, { target: { value: "{not json}" } });
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() =>
    expect(screen.getByText(/创建失败/)).toBeInTheDocument(),
  );
  expect(onCreated).not.toHaveBeenCalled();
});

// --- edit mode -----------------------------------------------------------

const editKb = {
  id: 1,
  name: "kb",
  method: "fast",
  settings: {
    llm: { model: "deepseek-chat", model_provider: "deepseek" },
    chunking: { size: 300 },
  },
};

test("edit mode pre-fills and PATCHes on submit", async () => {
  const patched: { url: string; body: unknown }[] = [];
  server.use(
    http.patch("/kbs/1", async ({ request }) => {
      const b = (await request.json()) as { name: string };
      patched.push({ url: request.url, body: b });
      return HttpResponse.json({
        id: 1,
        name: b.name,
        method: "fast",
        settings: {},
      });
    }),
  );
  const onSaved = vi.fn();
  render(
    <MemoryRouter>
      <KbForm kb={editKb as any} onSaved={onSaved} />
    </MemoryRouter>,
  );

  // pre-filled: model input shows deepseek-chat
  expect((screen.getByPlaceholderText("deepseek-chat") as HTMLInputElement).value).toBe(
    "deepseek-chat",
  );

  // submit -> PATCH (button label = 保存修改)
  await userEvent.click(screen.getByRole("button", { name: /保存修改/ }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const last = patched[patched.length - 1]?.body as { name: string; method: string };
  expect(last.name).toBe("kb");
  expect(last.method).toBe("fast");
});
