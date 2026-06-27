import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { EvidenceDrawer } from "./EvidenceDrawer";
import type { EvidenceDetail } from "../api/types";

const evidence: EvidenceDetail = {
  citation_id: "chunk:c2",
  matched: "Matched evidence text",
  before: "Before context",
  after: null,
  source: { document_id: 7, document_title: "alpha.md", chunk_id: "c2", ordinal: 1 },
};

test("does not render when closed", () => {
  render(<EvidenceDrawer open={false} loading={false} evidence={evidence} error={null} onClose={vi.fn()} />);
  expect(screen.queryByText("证据详情")).not.toBeInTheDocument();
});

test("renders matched evidence and missing context label", () => {
  render(<EvidenceDrawer open loading={false} evidence={evidence} error={null} onClose={vi.fn()} />);
  expect(screen.getByText("证据详情")).toBeInTheDocument();
  expect(screen.getByText("Matched evidence text")).toBeInTheDocument();
  expect(screen.getByText("Before context")).toBeInTheDocument();
  expect(screen.getByText("后文不可用")).toBeInTheDocument();
  expect(screen.getByText(/alpha.md/)).toBeInTheDocument();
});

test("renders loading and error states", () => {
  const { rerender } = render(<EvidenceDrawer open loading evidence={null} error={null} onClose={vi.fn()} />);
  expect(screen.getByText("加载证据…")).toBeInTheDocument();

  rerender(<EvidenceDrawer open loading={false} evidence={null} error="500 evidence" onClose={vi.fn()} />);
  expect(screen.getByText("证据加载失败")).toBeInTheDocument();
  expect(screen.getByText("500 evidence")).toBeInTheDocument();
});

test("close button calls onClose", () => {
  const onClose = vi.fn();
  render(<EvidenceDrawer open loading={false} evidence={evidence} error={null} onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: "关闭证据抽屉" }));
  expect(onClose).toHaveBeenCalledTimes(1);
});
