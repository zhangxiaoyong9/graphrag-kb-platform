import { render, screen } from "@testing-library/react";
import { CostPanel } from "./CostPanel";

test("renders total and per-step bars", () => {
  render(<CostPanel totalUsd={0.014} byStep={{ extract_graph: 0.01, summarize_descriptions: 0.004 }} />);
  expect(screen.getByText(/\$0\.014/)).toBeInTheDocument();
  expect(screen.getByText(/extract_graph/)).toBeInTheDocument();
  expect(screen.getByText(/summarize_descriptions/)).toBeInTheDocument();
});

test("renders em-dash when cost unknown", () => {
  render(<CostPanel totalUsd={null} byStep={{}} />);
  expect(screen.getByText(/—/)).toBeInTheDocument();
});

test("renders nothing for steps when byStep empty but total known", () => {
  const { container } = render(<CostPanel totalUsd={0.05} byStep={{}} />);
  expect(screen.getByText(/\$0\.0500/)).toBeInTheDocument();
  expect(container.querySelectorAll(".cost-row").length).toBe(0);
});
