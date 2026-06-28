import { renderHook, waitFor, act } from "@testing-library/react";
import { useJobEvents } from "./useJobEvents";

class MockWS {
  static last: MockWS | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    MockWS.last = this;
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
  emit(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
}

beforeEach(() => {
  MockWS.last = null;
  (global as unknown as { WebSocket: unknown }).WebSocket = MockWS;
});
afterEach(() => {
  delete (global as unknown as { WebSocket?: unknown }).WebSocket;
});

test("snapshot sets data, delta merges steps, terminal closes socket", async () => {
  const { result } = renderHook(() => useJobEvents(7));
  await waitFor(() => expect(MockWS.last).not.toBeNull());
  const ws = MockWS.last!;
  act(() => {
    ws.onopen?.();
    ws.emit({
      type: "snapshot", job: { id: 7, status: "running" },
      steps: [{ id: 1, name: "x", ordinal: 0, kind: "atomic", status: "pending", progress: null }],
    });
  });
  await waitFor(() => expect(result.current.connected).toBe(true));
  expect(result.current.data?.status).toBe("running");

  act(() => ws.emit({
    type: "delta", job: { id: 7, status: "running" },
    steps: [{ id: 1, name: "x", ordinal: 0, kind: "atomic", status: "succeeded", progress: null }],
  }));
  await waitFor(() => expect(result.current.data?.steps[0].status).toBe("succeeded"));

  act(() => ws.emit({ type: "delta", job: { id: 7, status: "succeeded" }, steps: [] }));
  await waitFor(() => expect(result.current.data?.status).toBe("succeeded"));
  expect(ws.closed).toBe(true);
});

test("disconnect sets connected=false", async () => {
  const { result } = renderHook(() => useJobEvents(8));
  await waitFor(() => expect(MockWS.last).not.toBeNull());
  act(() => MockWS.last!.onclose?.());
  await waitFor(() => expect(result.current.connected).toBe(false));
});

test("null jobId does nothing", () => {
  const { result } = renderHook(() => useJobEvents(null));
  expect(result.current.connected).toBe(false);
  expect(result.current.data).toBeNull();
});
