import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import AppShell from "../components/AppShell";
import ApiKeysPage from "./ApiKeysPage";
import SettingsPage from "./SettingsPage";
import AnalyticsPage from "./AnalyticsPage";
import DocumentsCenterPage from "./DocumentsCenterPage";

const server = setupServer(
  http.get("/kbs", () => HttpResponse.json([])),
  http.get("/health", () =>
    HttpResponse.json({ status: "ok", db: "ok", worker: { last_heartbeat_at: null, stale: false } }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function withRouter(el: React.ReactNode) {
  return <MemoryRouter>{el}</MemoryRouter>;
}

test("sidebar renders all nav groups", async () => {
  render(withRouter(<AppShell />));
  for (const title of ["工作台", "知识库", "检索与问答", "分析与监控", "系统管理"]) {
    expect(screen.getByText(title)).toBeInTheDocument();
  }
  expect(screen.getAllByText("概览").length).toBeGreaterThan(0);
  expect(screen.getByText("API Keys")).toBeInTheDocument();
});

test("ApiKeysPage states it is not enabled", async () => {
  render(withRouter(<ApiKeysPage />));
  expect(await screen.findByText("API Key 管理尚未启用")).toBeInTheDocument();
  expect(screen.getByText(/当前版本不提供/)).toBeInTheDocument();
});

test("SettingsPage is read-only guidance", async () => {
  render(withRouter(<SettingsPage />));
  expect(screen.getByText("只读说明")).toBeInTheDocument();
  expect(screen.getByText("LLM 模型")).toBeInTheDocument();
  expect(screen.getByText("查询方式")).toBeInTheDocument();
});

test("AnalyticsPage shows honest empty states when no data", async () => {
  render(withRouter(<AnalyticsPage />));
  // Honest empty states for unavailable metrics
  expect(await screen.findByText("暂无法绘制趋势")).toBeInTheDocument();
  expect(screen.getByText("暂无查询历史")).toBeInTheDocument();
});

test("DocumentsCenterPage empty state when no KBs", async () => {
  render(withRouter(<DocumentsCenterPage />));
  expect(await screen.findByText("还没有知识库")).toBeInTheDocument();
});
