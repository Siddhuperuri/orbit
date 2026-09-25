/**
 * A Server-Sent Events reader over a `fetch` body.
 *
 * `EventSource` cannot issue a `POST`, and the answer stream is a `POST` because
 * the question is user content that must stay out of URLs and access logs
 * (ADR-0016). So the stream is read by hand: bytes -> text -> events, per the
 * WHATWG SSE framing rules, tolerant of an event being split across network
 * chunks at any byte -- including in the middle of a multi-byte character.
 */

export interface SseMessage {
  /** The `event:` field; `"message"` when the server sent none. */
  event: string;
  /** All `data:` lines of the event, joined with `\n`. */
  data: string;
  id?: string;
}

// A blank line terminates an event. Three line-ending styles are legal.
const EVENT_BOUNDARY = /\r\n\r\n|\n\n|\r\r/;

function parseEvent(block: string): SseMessage | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];

  for (const line of block.split(/\r\n|\n|\r/)) {
    // Comment lines (heartbeats) and blanks carry nothing.
    if (line === "" || line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    // Exactly one leading space after the colon is part of the framing.
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
    // `retry` and unknown fields are ignored, as the spec directs.
  }

  // An event with no data lines is not dispatched.
  if (data.length === 0) return null;
  return id === undefined ? { event, data: data.join("\n") } : { event, data: data.join("\n"), id };
}

/**
 * Yields each complete event as it arrives. Stops when the stream ends or the
 * signal aborts; an event still incomplete at end-of-stream is discarded, as the
 * spec requires -- the server always terminates events with a blank line.
 */
export async function* readSse(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseMessage, void, void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort, { once: true });

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;

      // `stream: true` keeps a partial multi-byte sequence for the next chunk.
      buffer += decoder.decode(value, { stream: true });

      for (;;) {
        const match = EVENT_BOUNDARY.exec(buffer);
        if (!match) break;
        const block = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const message = parseEvent(block);
        if (message) yield message;
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    // Closing the connection is what tells the server to stop generating.
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
