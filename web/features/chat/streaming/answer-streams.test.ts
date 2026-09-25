import { describe, expect, it, vi } from "vitest";

import type { AnswerStreamEvent } from "@/features/chat/api/stream";
import { AnswerStreams, type FinishedAnswer } from "@/features/chat/streaming/answer-streams";
import type { Message } from "@/features/chat/types";
import { ApiError } from "@/lib/api/errors";

const message = { id: "m1", content: "Answer [S1]", ordinal: 2 } as Message;

async function* events(...items: AnswerStreamEvent[]): AsyncGenerator<AnswerStreamEvent> {
  for (const item of items) {
    await Promise.resolve();
    yield item;
  }
}

const started: AnswerStreamEvent = {
  type: "started",
  data: { conversation_id: "c", question_message_id: "q", answer_message_id: "a" },
};
const retrieval: AnswerStreamEvent = {
  type: "retrieval",
  data: { retrieved: 6, sources: 2, degraded: null, retrieval_ms: 12 },
};
const delta = (text: string): AnswerStreamEvent => ({ type: "delta", data: { text } });

describe("AnswerStreams", () => {
  it("runs a stream to completion and reports the finished message", async () => {
    const finished: FinishedAnswer[] = [];
    const streams = new AnswerStreams({
      stream: () =>
        events(started, retrieval, delta("An"), delta("swer"), { type: "done", data: message }),
      onFinished: (result) => finished.push(result),
    });

    await streams.ask("c1", "What?");

    expect(streams.getState("c1")).toEqual({ phase: "settled" });
    expect(finished).toEqual([
      { conversationId: "c1", question: "What?", outcome: { kind: "done", message } },
    ]);
  });

  it("exposes text as it arrives, before the stream ends", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: string[] = [];

    const streams = new AnswerStreams({
      stream: async function* () {
        yield retrieval;
        yield delta("Hello");
        await gate;
        yield { type: "done", data: message } as AnswerStreamEvent;
      },
    });
    streams.subscribe(() => {
      const state = streams.getState("c1");
      if (state.phase === "streaming") seen.push(state.text);
    });

    const running = streams.ask("c1", "q");
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(streams.getState("c1")).toMatchObject({ phase: "streaming", text: "Hello" });

    release();
    await running;
    expect(seen).toContain("Hello");
  });

  it("turns a failure before the stream opens (e.g. 409) into a failed state", async () => {
    const finished: FinishedAnswer[] = [];
    const streams = new AnswerStreams({
      stream: async function* () {
        throw new ApiError({
          code: "ANSWER_IN_PROGRESS",
          status: 409,
          message: "Busy.",
          requestId: "r1",
        });
      },
      onFinished: (result) => finished.push(result),
    });

    await streams.ask("c1", "q");

    expect(streams.getState("c1")).toMatchObject({
      phase: "failed",
      error: { code: "ANSWER_IN_PROGRESS", request_id: "r1" },
    });
    expect(finished[0]?.outcome.kind).toBe("failed");
  });

  it("stops on request and reports it as stopped, not failed", async () => {
    const finished: FinishedAnswer[] = [];
    const streams = new AnswerStreams({
      stream: async function* (_id, _body, signal) {
        yield retrieval;
        await new Promise<void>((_resolve, reject) =>
          signal.addEventListener("abort", () => reject(new DOMException("stopped", "AbortError"))),
        );
      },
      onFinished: (result) => finished.push(result),
    });

    const running = streams.ask("c1", "q");
    await new Promise((resolve) => setTimeout(resolve, 5));
    streams.stop("c1");
    await running;

    expect(streams.getState("c1")).toEqual({ phase: "idle" });
    expect(finished[0]?.outcome).toEqual({ kind: "stopped" });
  });

  it("refuses a second question while one is in flight in the same conversation", async () => {
    const stream = vi.fn(() =>
      events(retrieval, delta("x"), { type: "done", data: message } as AnswerStreamEvent),
    );
    const streams = new AnswerStreams({ stream });

    const first = streams.ask("c1", "one");
    const second = streams.ask("c1", "two");
    await Promise.all([first, second]);

    expect(stream).toHaveBeenCalledTimes(1);
  });

  it("runs different conversations independently", async () => {
    const streams = new AnswerStreams({
      stream: () => events(retrieval, { type: "done", data: message } as AnswerStreamEvent),
    });
    await Promise.all([streams.ask("a", "q"), streams.ask("b", "q")]);
    expect(streams.getState("a")).toEqual({ phase: "settled" });
    expect(streams.getState("b")).toEqual({ phase: "settled" });
  });

  it("retries the last question with the same document scope", async () => {
    let calls = 0;
    const bodies: unknown[] = [];
    const streams = new AnswerStreams({
      stream: (_id, body) => {
        bodies.push(body);
        calls += 1;
        return calls === 1
          ? events(retrieval, {
              type: "error",
              data: {
                code: "GENERATION_TIMEOUT",
                message: "slow",
                request_id: "r",
                retryable: true,
              },
            } as AnswerStreamEvent)
          : events(retrieval, { type: "done", data: message } as AnswerStreamEvent);
      },
    });

    await streams.ask("c1", "Q", ["doc-1"]);
    expect(streams.getState("c1").phase).toBe("failed");

    await streams.retry("c1");
    expect(streams.getState("c1").phase).toBe("settled");
    expect(bodies[1]).toEqual({ question: "Q", documentIds: ["doc-1"] });
  });

  it("aborts everything on dispose", async () => {
    let aborted = false;
    const streams = new AnswerStreams({
      stream: async function* (_id, _body, signal) {
        yield retrieval;
        await new Promise<void>((_resolve, reject) =>
          signal.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("x", "AbortError"));
          }),
        );
      },
    });
    const running = streams.ask("c1", "q");
    await new Promise((resolve) => setTimeout(resolve, 5));
    streams.dispose();
    await running;
    expect(aborted).toBe(true);
  });
});
