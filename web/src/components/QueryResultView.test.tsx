import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryResultView } from "./QueryResultView";
import type { QueryResult } from "../api/types";

// Fixture uses snake_case to mirror the actual wire format from the backend
// (QueryResultOut: elapsed_ms / prompt_tokens / output_tokens / llm_calls).
// If someone reverts the fields to camelCase, this fixture + tsc will catch it.
const r: QueryResult = {
  answer: "A",
  method: "local",
  error: null,
  elapsed_ms: 42,
  prompt_tokens: 5,
  output_tokens: 9,
  llm_calls: 1,
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

test("renders the truncated notice when result.truncated is true", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, truncated: true }} /></MemoryRouter>);
  expect(screen.getByText(/结果已达行数上限/)).toBeInTheDocument();
});

test("omits the truncated notice when result.truncated is falsy", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.queryByText(/结果已达行数上限/)).not.toBeInTheDocument();
});

test("renders Cypher in a collapsible details when result.cypher is set", () => {
  render(<MemoryRouter><QueryResultView result={{ ...r, cypher: "MATCH (n) RETURN n" }} /></MemoryRouter>);
  // <summary> is always visible — proves the section rendered because cypher was set.
  expect(screen.getByText("生成的 Cypher")).toBeInTheDocument();
  // expand and confirm the cypher body is present
  fireEvent.click(screen.getByText("生成的 Cypher"));
  expect(screen.getByText("MATCH (n) RETURN n")).toBeInTheDocument();
});

test("omits the Cypher section when result.cypher is absent", () => {
  render(<MemoryRouter><QueryResultView result={r} /></MemoryRouter>);
  expect(screen.queryByText("生成的 Cypher")).not.toBeInTheDocument();
});
