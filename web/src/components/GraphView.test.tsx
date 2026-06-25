import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import type { GraphData } from "../api/types";
import * as client from "../api/client";

// react-force-graph-2d is mocked globally in setupTests.ts (jsdom has no canvas).

import { GraphView } from "./GraphView";

const fixture: GraphData = {
  nodes: [
    { id: "n1", title: "Alpha", type: "organization", degree: 5, community: "c1" },
    { id: "n2", title: "Beta", type: "person", degree: 3, community: "c2" },
  ],
  edges: [{ source: "n1", target: "n2", weight: 2, description: "knows" }],
};

test("fetches graph on mount and renders the force-graph container", async () => {
  const spy = vi.spyOn(client, "getGraph").mockResolvedValue(fixture);
  render(<GraphView kbId={1} />);
  await waitFor(() => expect(spy).toHaveBeenCalledWith(1, expect.anything()));
  expect(await screen.findByTestId("force-graph")).toBeInTheDocument();
});

test("shows node count note", async () => {
  vi.spyOn(client, "getGraph").mockResolvedValue(fixture);
  render(<GraphView kbId={1} />);
  expect(await screen.findByText(/共 2 个节点/)).toBeInTheDocument();
});

test("search input refetches with q and hop=2", async () => {
  const spy = vi.spyOn(client, "getGraph").mockResolvedValue(fixture);
  render(<GraphView kbId={1} />);
  await waitFor(() => expect(spy).toHaveBeenCalled());
  const input = await screen.findByPlaceholderText(/搜索实体/);
  fireEvent.change(input, { target: { value: "alpha" } });
  fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
  await waitFor(() => {
    const lastCall = spy.mock.calls[spy.mock.calls.length - 1];
    expect(lastCall[0]).toBe(1);
    expect(lastCall[1]?.q).toBe("alpha");
    expect(lastCall[1]?.hop).toBe(2);
  });
});

test("shows capped note when node count equals limit", async () => {
  // With limit=2 and 2 nodes returned, treat as capped.
  vi.spyOn(client, "getGraph").mockResolvedValue({ ...fixture });
  render(<GraphView kbId={1} limit={2} />);
  expect(await screen.findByText(/共 2 个节点/)).toBeInTheDocument();
  expect(await screen.findByText(/已达上限/)).toBeInTheDocument();
});
