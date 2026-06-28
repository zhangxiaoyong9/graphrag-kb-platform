import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { DocumentManager } from "./DocumentManager";
import type { DocumentOut } from "../api/types";
import * as client from "../api/client";

const docs: DocumentOut[] = [
  { id: 1, title: "alpha.md", status: "ready", bytes: 2048, chunk_count: 3 },
  { id: 2, title: "beta.txt", status: null, bytes: 0, chunk_count: 0 },
];

function renderManager(reload = vi.fn()) {
  return render(
    <MemoryRouter>
      <DocumentManager kbId={1} docs={docs} reload={reload} />
    </MemoryRouter>,
  );
}

test("renders document rows with title, bytes, chunks, status", () => {
  renderManager();
  expect(screen.getByText("alpha.md")).toBeInTheDocument();
  expect(screen.getByText(/2(\.0)? KB|2,048 B/)).toBeInTheDocument();
  expect(screen.getByText(/3 个分块/)).toBeInTheDocument();
  expect(screen.getByText(/0 个分块/)).toBeInTheDocument();
});

test("renders a detail link for each document", () => {
  renderManager();
  expect(screen.getByRole("link", { name: "查看文档 alpha.md" })).toHaveAttribute("href", "/kbs/1/documents/1");
  expect(screen.getByRole("link", { name: "查看文档 beta.txt" })).toHaveAttribute("href", "/kbs/1/documents/2");
});

test("shows a file input for multipart upload", () => {
  renderManager();
  expect(screen.getByLabelText(/上传文件/)).toBeInTheDocument();
});

test("delete button confirms with auto-rebuild copy and calls deleteDocument", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue({ shrinkJobCreated: false });
  const reload = vi.fn();
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderManager(reload);
  fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
  expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/自动重建图谱/));
  await waitFor(() => expect(spy).toHaveBeenCalledWith(1, 1));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  spy.mockRestore();
  confirmSpy.mockRestore();
});

test("delete is cancelled when confirm returns false", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue({ shrinkJobCreated: false });
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderManager();
  fireEvent.click(screen.getAllByRole("button", { name: /删除/ })[0]);
  expect(spy).not.toHaveBeenCalled();
  spy.mockRestore();
});
