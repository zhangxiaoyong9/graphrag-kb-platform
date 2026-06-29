import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import QueryPresetsPage from "./QueryPresetsPage";

const server = setupServer(
  http.get("/query-presets", () =>
    HttpResponse.json([
      { id: 1, name: "默认", description: "", method: "local", is_builtin: true },
    ]),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("lists built-in presets with no delete control", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByText("默认")).toBeInTheDocument();
  // only builtin row present -> no 删除 button (title="删除") rendered
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /删除/ })).not.toBeInTheDocument(),
  );
});

test("renders the create form", async () => {
  render(<QueryPresetsPage />);
  expect(await screen.findByPlaceholderText("名称")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /新建/ })).toBeInTheDocument();
});
