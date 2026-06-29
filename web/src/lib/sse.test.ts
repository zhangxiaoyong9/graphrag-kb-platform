import { parseSse } from "./sse";

function sseResponse(body: string): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(enc.encode(body));
      controller.close();
    },
  });
  return new Response(stream, { headers: { "content-type": "text/event-stream" } });
}

test("parses meta/delta/done events", async () => {
  const body =
    'event: meta\ndata: {"method":"local"}\n\n' +
    'event: delta\ndata: {"text":"Hello "}\n\n' +
    'event: delta\ndata: {"text":"world"}\n\n' +
    'event: done\ndata: {"result":{"answer":"Hello world"}}\n\n';
  const events = [];
  for await (const ev of parseSse(sseResponse(body))) events.push(ev);
  expect(events.map((e) => e.event)).toEqual(["meta", "delta", "delta", "done"]);
  expect(events[1].data).toEqual({ text: "Hello " });
  expect(events[3].data.result.answer).toBe("Hello world");
});

test("handles chunk boundaries splitting a line", async () => {
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(enc.encode('event: delta\ndata: {"text":"abc'));
      controller.enqueue(enc.encode('def"}\n\n'));
      controller.close();
    },
  });
  const events = [];
  for await (const ev of parseSse(new Response(stream))) events.push(ev);
  expect(events).toEqual([{ event: "delta", data: { text: "abcdef" } }]);
});
