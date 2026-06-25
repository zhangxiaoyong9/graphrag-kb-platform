import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryResultView } from "./QueryResultView";
import type { QueryResult } from "../api/types";

const r: QueryResult = {
  answer: "A",
  method: "local",
  error: null,
  elapsedMs: 42,
  promptTokens: 5,
  outputTokens: 9,
  llmCalls: 1,
  sources: [
    { kind: "entity", name: "宁德时代", text: "电池厂商" },
    { kind: "text_unit", name: "1", text: "一段来源片段" },
  ],
};

test("renders sources, tokens and server elapsed", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.getByText("宁德时代")).toBeInTheDocument();
  expect(screen.getByText(/一段来源片段/)).toBeInTheDocument();
  expect(screen.getByText(/42/)).toBeInTheDocument(); // elapsed
  expect(screen.getByText(/5.*9/)).toBeInTheDocument(); // tokens
});

test("hides sources section when none", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, sources: undefined }} /></MemoryRouter>);
  expect(screen.queryByText("引用与来源")).not.toBeInTheDocument();
});
