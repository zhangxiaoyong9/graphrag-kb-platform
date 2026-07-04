import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import LlmHealthPage from "./LlmHealthPage";

const OK = {
  profiles: [
    { provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" },
    { provider: "deepseek", model: "deepseek-chat", api_base: "https://api.deepseek.com", state: "open" },
    { provider: "ollama", model: "qwen2", api_base: "http://localhost:11434", state: "half_open" },
  ],
  metrics: { ttft_ms_p50: 150, failover_detect_ms_p50: 80, failover_recover_ms_p50: 1200, failovers: 3, successes: 50 },
};

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("renders breaker states with color badges, metrics, and the server-only caveat", async () => {
  server.use(http.get("/llm/health", () => HttpResponse.json(OK)));
  render(<LlmHealthPage />);
  // three states rendered
  expect(await screen.findByText("正常")).toBeInTheDocument();
  expect(screen.getByText("熔断")).toBeInTheDocument();
  expect(screen.getByText("半开")).toBeInTheDocument();
  // a metric value (TTFT p50 rounded)
  expect(screen.getByText("150 ms")).toBeInTheDocument();
  // the hard-requirement caveat
  expect(screen.getByText(/仅反映 API server 进程/)).toBeInTheDocument();
});

test("shows — for null metrics", async () => {
  server.use(
    http.get("/llm/health", () =>
      HttpResponse.json({
        profiles: [{ provider: "openai", model: "gpt-4o-mini", api_base: null, state: "closed" }],
        metrics: { ttft_ms_p50: null, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 0, successes: 0 },
      }),
    ),
  );
  render(<LlmHealthPage />);
  await screen.findByText("正常");
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3); // the three null p50 cards
});

test("empty state when no profiles", async () => {
  server.use(
    http.get("/llm/health", () =>
      HttpResponse.json({ profiles: [], metrics: { ttft_ms_p50: null, failover_detect_ms_p50: null, failover_recover_ms_p50: null, failovers: 0, successes: 0 } }),
    ),
  );
  render(<LlmHealthPage />);
  expect(await screen.findByText("暂无数据")).toBeInTheDocument();
});

test("error state with retry re-fetches", async () => {
  let calls = 0;
  server.use(
    http.get("/llm/health", () => {
      calls += 1;
      return new HttpResponse(null, { status: 500 });
    }),
  );
  render(<LlmHealthPage />);
  const retry = await screen.findByRole("button", { name: /重试/ });
  expect(screen.getByText(/加载失败/)).toBeInTheDocument();
  fireEvent.click(retry);
  await waitFor(() => expect(calls).toBe(2));
});

test("refresh button re-fetches", async () => {
  let calls = 0;
  server.use(
    http.get("/llm/health", () => {
      calls += 1;
      return HttpResponse.json(OK);
    }),
  );
  render(<LlmHealthPage />);
  await screen.findByText("正常");
  const refresh = screen.getByRole("button", { name: /刷新/ });
  fireEvent.click(refresh);
  await waitFor(() => expect(calls).toBe(2));
});
