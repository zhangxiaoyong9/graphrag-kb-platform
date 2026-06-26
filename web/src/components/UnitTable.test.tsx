import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import UnitTable from "./UnitTable";

const makeUnits = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    subject_id: `c${i}`,
    status: "succeeded",
    error: null,
    llm_raw_output: null,
    needs_reconsolidation: false,
  }));

const server = setupServer(
  http.get("/steps/1/units", ({ request }) => {
    const url = new URL(request.url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 20);
    const status = url.searchParams.get("status");
    const all = makeUnits(45).filter((u) => (status ? u.status === status : true));
    return HttpResponse.json({ items: all.slice(offset, offset + limit), total: all.length });
  }),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("paginates 20 per page with controls", async () => {
  render(<UnitTable stepId={1} active={false} />);
  // page 1: first 20 items + indicator "第 1–20 条 / 共 45 条"
  expect(await screen.findByText("c0")).toBeInTheDocument();
  expect(screen.getByText(/1–20.*45/)).toBeInTheDocument();
  expect(screen.queryByText("c20")).not.toBeInTheDocument(); // page 2 item not shown
  // next page
  await userEvent.click(screen.getByRole("button", { name: /下一页/ }));
  expect(await screen.findByText("c20")).toBeInTheDocument();
  expect(screen.getByText(/21–40.*45/)).toBeInTheDocument();
});
