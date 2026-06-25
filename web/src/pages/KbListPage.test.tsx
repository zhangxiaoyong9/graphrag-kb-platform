import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter } from "react-router-dom";
import KbListPage from "./KbListPage";

let store = [{ id: 1, name: "demo", method: "standard" }];
const server = setupServer(
  http.get("/kbs", () => HttpResponse.json(store)),
  http.post("/kbs", async ({ request }) => {
    const body = (await request.json()) as { name: string };
    const kb = { id: 2, name: body.name, method: "standard" };
    store = [...store, kb];
    return HttpResponse.json(kb);
  }),
);
beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  store = [{ id: 1, name: "demo", method: "standard" }];
});
afterAll(() => server.close());

test("lists kbs and creates one", async () => {
  render(
    <MemoryRouter>
      <KbListPage />
    </MemoryRouter>,
  );
  expect(await screen.findByText("demo")).toBeInTheDocument();
  await userEvent.type(screen.getByPlaceholderText("name"), "newkb");
  await userEvent.click(screen.getByText("Create KB"));
  expect(await screen.findByText("newkb")).toBeInTheDocument();
});
