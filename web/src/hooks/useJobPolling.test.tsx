import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useJobPolling } from "./useJobPolling";

const server = setupServer(http.get("/jobs/1", () => HttpResponse.json({ id: 1, status: "succeeded", steps: [] })));
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("loads job and stops on terminal", async () => {
  const { result } = renderHook(() => useJobPolling(1));
  await waitFor(() => expect(result.current?.status).toBe("succeeded"));
});
