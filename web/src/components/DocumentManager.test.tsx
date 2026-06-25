import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { DocumentManager } from "./DocumentManager";
import type { DocumentOut } from "../api/types";
import * as client from "../api/client";

const docs: DocumentOut[] = [
  { id: 1, title: "alpha.md", status: "ready", bytes: 2048, chunk_count: 3 },
  { id: 2, title: "beta.txt", status: null, bytes: 0, chunk_count: 0 },
];

test("renders document rows with title, bytes, chunks, status", () => {
  render(<DocumentManager kbId={1} docs={docs} reload={vi.fn()} />);
  expect(screen.getByText("alpha.md")).toBeInTheDocument();
  expect(screen.getByText(/2(\.0)? KB|2,048 B/)).toBeInTheDocument();
  expect(screen.getByText(/3 chunks/)).toBeInTheDocument();
  expect(screen.getByText(/0 chunks/)).toBeInTheDocument();
});

test("shows a file input for multipart upload", () => {
  render(<DocumentManager kbId={1} docs={docs} reload={vi.fn()} />);
  expect(screen.getByLabelText(/upload file/i)).toBeInTheDocument();
});

test("delete button confirms with graph-not-shrunk copy and calls deleteDocument", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue(undefined);
  const reload = vi.fn();
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DocumentManager kbId={1} docs={docs} reload={reload} />);
  fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]);
  expect(confirmSpy).toHaveBeenCalledWith(
    expect.stringMatching(/graph will (NOT|not) shrink/i),
  );
  await waitFor(() => expect(spy).toHaveBeenCalledWith(1, 1));
  await waitFor(() => expect(reload).toHaveBeenCalled());
  spy.mockRestore();
  confirmSpy.mockRestore();
});

test("delete is cancelled when confirm returns false", async () => {
  const spy = vi.spyOn(client, "deleteDocument").mockResolvedValue(undefined);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<DocumentManager kbId={1} docs={docs} reload={vi.fn()} />);
  fireEvent.click(screen.getAllByRole("button", { name: /delete/i })[0]);
  expect(spy).not.toHaveBeenCalled();
  spy.mockRestore();
});
