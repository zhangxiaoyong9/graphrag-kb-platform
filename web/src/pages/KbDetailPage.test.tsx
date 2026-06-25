import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import KbDetailPage from "./KbDetailPage";

let store = [{ id: 7, status: "succeeded" }];
const server = setupServer(
  http.get("/kbs/1", () => HttpResponse.json({ id: 1, name: "demo", method: "standard" })),
  http.get("/kbs/1/documents", () => HttpResponse.json([{ id: 1, title: "doc1", status: "parsed" }])),
  http.get("/kbs/1/jobs", () => HttpResponse.json(store)),
  http.post("/kbs/1/jobs", () => {
    const job = { id: 8, status: "pending" };
    store = [...store, job];
    return HttpResponse.json(job);
  }),
);
beforeAll(() => server.listen());
afterEach(() => {
  server.resetHandlers();
  store = [{ id: 7, status: "succeeded" }];
});
afterAll(() => server.close());

test("shows kb, documents, jobs; trigger adds a job", async () => {
  render(<MemoryRouter initialEntries={["/kbs/1"]}><Routes><Route path="/kbs/:id" element={<KbDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("demo")).toBeInTheDocument();
  expect(screen.getByText("doc1")).toBeInTheDocument();
  expect(await screen.findByText("job 7")).toBeInTheDocument();
  await userEvent.click(screen.getByText("Trigger Index"));
  expect(await screen.findByText("job 8")).toBeInTheDocument();
});
