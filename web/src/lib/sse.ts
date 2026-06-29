/** Parse a `text/event-stream` fetch Response into SSE events.
 *
 * `EventSource` only supports GET; our endpoints are POST with a JSON body, so
 * we read `response.body` and frame `event:` / `data:` / blank-line ourselves.
 */
export type SseEvent = { event: string; data: any };

export async function* parseSse(resp: Response): AsyncGenerator<SseEvent> {
  if (!resp.body) return;
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let dataLines: string[] = [];
  const flush = function* (): Generator<SseEvent> {
    if (event) {
      yield { event, data: dataLines.length ? JSON.parse(dataLines.join("")) : {} };
    }
    event = "";
    dataLines = [];
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep the last (possibly partial) line
    for (const line of lines) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      else if (line === "") yield* flush();
    }
  }
  // flush a trailing event without a terminating blank line
  buffer += decoder.decode();
  if (buffer.includes("\n")) {
    for (const line of buffer.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
  }
  yield* flush();
}
