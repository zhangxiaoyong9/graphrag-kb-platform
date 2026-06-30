import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { vi } from "vitest";
import KbForm from "./KbForm";

const captured: { url: string; body: unknown }[] = [];

const server = setupServer(
  // provider profiles: two llm, one embedding
  http.get("/provider-profiles", ({ request }) => {
    const kind = new URL(request.url).searchParams.get("kind");
    if (kind === "embedding") {
      return HttpResponse.json([
        { id: 3, name: "Ollama", kind: "embedding", provider: "ollama", model: "nomic-embed-text", api_base: "http://localhost:11434", api_version: null, structured_output: true, api_keys_count: 1 },
      ]);
    }
    return HttpResponse.json([
      { id: 1, name: "DS", kind: "llm", provider: "deepseek", model: "deepseek-chat", api_base: null, api_version: null, structured_output: false, api_keys_count: 1 },
      { id: 2, name: "OpenAI", kind: "llm", provider: "openai", model: "gpt-4o-mini", api_base: null, api_version: null, structured_output: true, api_keys_count: 1 },
    ]);
  }),
  http.post("/kbs", async ({ request }) => {
    const body = (await request.json()) as { name: string; method?: string };
    captured.push({ url: request.url, body });
    return HttpResponse.json({ id: 1, name: body.name, method: body.method ?? "standard" });
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

test("form has content sections and an LLM profile selector", async () => {
  renderForm();
  for (const label of [
    "分块 Chunking",
    "图谱抽取 Extract Graph",
    "聚类 Clustering",
  ]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  // LLM profile select loads from /provider-profiles
  expect(await screen.findByLabelText(/LLM 配置/)).toBeInTheDocument();
});

test("submit is blocked until an LLM profile is selected", async () => {
  const onCreated = renderForm();
  await screen.findByLabelText(/LLM 配置/);
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "my-kb");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));
  expect(onCreated).not.toHaveBeenCalled();
});

test("selecting an LLM profile submits its id and content settings_yaml", async () => {
  const onCreated = renderForm();
  const select = await screen.findByLabelText(/LLM 配置/);
  await userEvent.selectOptions(select, "1"); // DS
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "my-kb");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]?.body as {
    llm_profile_id: number;
    embedding_profile_id: number | null;
    name: string;
    settings_yaml: string;
  };
  expect(last.llm_profile_id).toBe(1);
  expect(last.embedding_profile_id).toBeNull();
  expect(last.name).toBe("my-kb");
  expect(typeof last.settings_yaml).toBe("string");
});

test("prompts section renders view-default toggles and shows fetched default", async () => {
  renderForm();
  expect(
    screen.getByText("提示词 Prompts（留空=用 graphrag 默认）"),
  ).toBeInTheDocument();
  // ensure mounted (profile fetch) before querying buttons
  await screen.findByLabelText(/LLM 配置/);
  // 7 view-default buttons: 3 indexing prompts (extract/summarize/report) + 4 query prompts
  const viewButtons = screen.getAllByRole("button", {
    name: /查看 graphrag 默认/,
  });
  expect(viewButtons).toHaveLength(7);
  await userEvent.click(viewButtons[0]);
  await waitFor(() =>
    expect(screen.getByText("DEFAULT_EXTRACT")).toBeInTheDocument(),
  );
});

test("advanced override replaces form-built settings when non-empty", async () => {
  const onCreated = renderForm();
  await screen.findByLabelText(/LLM 配置/);
  await userEvent.selectOptions(screen.getByLabelText(/LLM 配置/), "1");
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "ovr-kb");
  await userEvent.click(screen.getByRole("button", { name: /高级/ }));
  const ta = screen.getByLabelText(/原始 settings_yaml/);
  fireEvent.change(ta, {
    target: { value: '{"chunking":{"size":300}}' },
  });
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]?.body as {
    settings_yaml: string;
  };
  const parsed = JSON.parse(last.settings_yaml);
  expect(parsed.chunking.size).toBe(300);
});

test("bad JSON in advanced override shows inline error and does not submit", async () => {
  const onCreated = renderForm();
  await screen.findByLabelText(/LLM 配置/);
  await userEvent.selectOptions(screen.getByLabelText(/LLM 配置/), "1");
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

test("chunking strategy defaults to markdown and serializes on submit", async () => {
  const onCreated = renderForm();
  await screen.findByLabelText(/LLM 配置/);
  const select = screen.getByLabelText(/切片方式/) as HTMLSelectElement;
  expect(select.value).toBe("markdown");
  await userEvent.selectOptions(screen.getByLabelText(/LLM 配置/), "1");
  await userEvent.type(screen.getByPlaceholderText(/请输入知识库名称/), "md-kb");
  await userEvent.click(screen.getByRole("button", { name: /创建知识库/ }));

  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const last = captured[captured.length - 1]?.body as { settings_yaml: string };
  expect(JSON.parse(last.settings_yaml).chunking.strategy).toBe("markdown");
});

test("overlap input is disabled in markdown mode, enabled for tokens", async () => {
  renderForm();
  await screen.findByLabelText(/LLM 配置/);
  const overlap = screen.getByLabelText(/^overlap$/) as HTMLInputElement;
  expect(overlap.disabled).toBe(true); // default markdown -> overlap not used
  await userEvent.selectOptions(screen.getByLabelText(/切片方式/), "tokens");
  expect(overlap.disabled).toBe(false); // tokens -> overlap relevant again
  await userEvent.selectOptions(screen.getByLabelText(/切片方式/), "markdown");
  expect(overlap.disabled).toBe(true);
});

// --- edit mode -----------------------------------------------------------

const editKb = {
  id: 1,
  name: "kb",
  method: "fast",
  settings: { chunking: { size: 300 } },
  llm_profile: { id: 2, name: "OpenAI", provider: "openai", model: "gpt-4o-mini" },
  embedding_profile: null,
};

test("edit mode pre-selects the kb LLM profile and PATCHes its id", async () => {
  const patched: { url: string; body: unknown }[] = [];
  server.use(
    http.patch("/kbs/1", async ({ request }) => {
      const b = (await request.json()) as { name: string; method: string };
      patched.push({ url: request.url, body: b });
      return HttpResponse.json({
        id: 1,
        name: b.name,
        method: "fast",
        settings: {},
        llm_profile: null,
        embedding_profile: null,
      });
    }),
  );
  const onSaved = vi.fn();
  render(
    <MemoryRouter>
      <KbForm kb={editKb as never} onSaved={onSaved} />
    </MemoryRouter>,
  );

  // pre-selected profile = OpenAI (id 2)
  const select = await screen.findByLabelText(/LLM 配置/);
  expect((select as HTMLSelectElement).value).toBe("2");

  // submit -> PATCH (button label = 保存修改)
  await userEvent.click(screen.getByRole("button", { name: /保存修改/ }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const last = patched[patched.length - 1]?.body as {
    name: string;
    method: string;
    llm_profile_id: number;
  };
  expect(last.name).toBe("kb");
  expect(last.method).toBe("fast");
  expect(last.llm_profile_id).toBe(2);
});
