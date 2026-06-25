import "@testing-library/jest-dom";
import { vi } from "vitest";
import React from "react";

// react-force-graph-2d renders to <canvas>; jsdom has no canvas context.
// Mock it globally (as a forwardRef so attaching a ref doesn't warn) so any
// component using it (incl. indirectly via pages) renders a stub instead of
// crashing in the kapsule/d3 canvas init.
vi.mock("react-force-graph-2d", () => {
  const Stub = React.forwardRef(() =>
    React.createElement("div", { "data-testid": "force-graph" }),
  );
  Stub.displayName = "ForceGraph2DStub";
  return { default: Stub };
});
