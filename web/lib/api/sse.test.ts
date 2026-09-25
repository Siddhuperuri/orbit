import { describe, expect, it } from "vitest";

import { readSse, type SseMessage } from "@/lib/api/sse";

const encoder = new TextEncoder();

function streamOf(chunks: Array<string | Uint8Array>): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseMessage[]> {
  const messages: SseMessage[] = [];
  for await (const message of readSse(stream)) messages.push(message);
  return messages;
}

describe("readSse", () => {
  it("parses the backend's framing: event, data, blank line", async () => {
    const messages = await collect(
      streamOf([
        'event: started\ndata: {"conversation_id":"c"}\n\n',
        'event: delta\ndata: {"text":"Hel"}\n\n',
        'event: delta\ndata: {"text":"lo"}\n\n',
        'event: done\ndata: {"id":"m"}\n\n',
      ]),
    );

    expect(messages.map((m) => m.event)).toEqual(["started", "delta", "delta", "done"]);
    expect(JSON.parse(messages[1]!.data)).toEqual({ text: "Hel" });
  });

  it("reassembles an event split at any byte boundary", async () => {
    const whole = 'event: delta\ndata: {"text":"split me"}\n\n';
    const chunks = whole.split("");
    const messages = await collect(streamOf(chunks));

    expect(messages).toHaveLength(1);
    expect(messages[0]!.event).toBe("delta");
    expect(JSON.parse(messages[0]!.data)).toEqual({ text: "split me" });
  });

  it("does not corrupt a multi-byte character split across chunks", async () => {
    const bytes = encoder.encode('event: delta\ndata: {"text":"naïve — 日本語"}\n\n');
    // Cut inside the two-byte "ï" and the three-byte "日".
    const messages = await collect(
      streamOf([bytes.slice(0, 27), bytes.slice(27, 41), bytes.slice(41)]),
    );

    expect(JSON.parse(messages[0]!.data)).toEqual({ text: "naïve — 日本語" });
  });

  it("accepts CRLF and joins multiple data lines with a newline", async () => {
    const messages = await collect(streamOf(["event: e\r\ndata: one\r\ndata: two\r\n\r\n"]));
    expect(messages).toEqual([{ event: "e", data: "one\ntwo" }]);
  });

  it("ignores comments and heartbeats, and defaults the event name", async () => {
    const messages = await collect(streamOf([": heartbeat\n\n", "data: plain\n\n"]));
    expect(messages).toEqual([{ event: "message", data: "plain" }]);
  });

  it("strips exactly one leading space from a value", async () => {
    const messages = await collect(streamOf(["data:  two spaces\n\n"]));
    expect(messages[0]!.data).toBe(" two spaces");
  });

  it("discards an event the stream ended in the middle of", async () => {
    const messages = await collect(
      streamOf(["event: delta\ndata: complete\n\nevent: delta\ndata: cut"]),
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]!.data).toBe("complete");
  });

  it("stops reading when the signal aborts", async () => {
    const controller = new AbortController();
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode("event: delta\ndata: one\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });

    const received: string[] = [];
    for await (const message of readSse(stream, controller.signal)) {
      received.push(message.data);
      controller.abort();
      break;
    }

    expect(received).toEqual(["one"]);
    expect(cancelled).toBe(true);
  });
});
