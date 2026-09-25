import { describe, expect, it } from "vitest";

import {
  initialStreamState,
  isBusy,
  streamReducer,
  type StreamAction,
  type StreamState,
} from "@/features/chat/state/stream-state";
import type { ErrorEvent, Message } from "@/features/chat/types";

const message = { id: "m", content: "Final answer [S1]" } as Message;
const failure: ErrorEvent = {
  code: "GENERATION_TIMEOUT",
  message: "The model took too long.",
  request_id: "req-1",
  retryable: true,
};

function run(actions: StreamAction[], from: StreamState = initialStreamState): StreamState {
  return actions.reduce(streamReducer, from);
}

describe("streamReducer", () => {
  it("follows the happy path from question to settled answer", () => {
    const state = run([
      { type: "submit", question: "What changed?" },
      { type: "retrieval", retrieved: 8, sources: 3, degraded: null },
      { type: "delta", text: "The " },
      { type: "delta", text: "budget" },
    ]);
    expect(state).toMatchObject({ phase: "streaming", text: "The budget", sources: 3 });
    expect(streamReducer(state, { type: "done", message })).toEqual({ phase: "settled" });
  });

  it("accumulates deltas in order", () => {
    const state = run([
      { type: "submit", question: "q" },
      { type: "retrieval", retrieved: 1, sources: 1, degraded: null },
      ...["a", "b", "c"].map((text): StreamAction => ({ type: "delta", text })),
    ]);
    expect(state).toMatchObject({ text: "abc" });
  });

  it("refuses a second question while one is in flight", () => {
    const busy = run([{ type: "submit", question: "first" }]);
    expect(streamReducer(busy, { type: "submit", question: "second" })).toBe(busy);
    expect(isBusy(busy)).toBe(true);
  });

  it("keeps the partial answer the server recorded when generation fails", () => {
    const partial = { id: "p", content: "The budg" } as Message;
    const state = run([
      { type: "submit", question: "q" },
      { type: "retrieval", retrieved: 1, sources: 1, degraded: null },
      { type: "delta", text: "The budg" },
      { type: "error", error: { ...failure, answer: partial } },
    ]);
    expect(state).toMatchObject({ phase: "failed", partial, question: "q" });
  });

  it("fails cleanly when retrieval fails before any text", () => {
    const state = run([
      { type: "submit", question: "q" },
      { type: "error", error: { ...failure, code: "RETRIEVAL_FAILED" } },
    ]);
    expect(state).toMatchObject({ phase: "failed", partial: null });
  });

  it("ignores a late delta after the stream has failed", () => {
    const failed = run([
      { type: "submit", question: "q" },
      { type: "error", error: failure },
    ]);
    expect(streamReducer(failed, { type: "delta", text: "late" })).toBe(failed);
  });

  it("ignores events when nothing is in flight", () => {
    expect(streamReducer(initialStreamState, { type: "delta", text: "x" })).toBe(
      initialStreamState,
    );
    expect(streamReducer(initialStreamState, { type: "done", message })).toBe(initialStreamState);
    expect(streamReducer(initialStreamState, { type: "error", error: failure })).toBe(
      initialStreamState,
    );
  });

  it("starts streaming on a delta even if the retrieval event was missed", () => {
    const state = run([
      { type: "submit", question: "q" },
      { type: "delta", text: "Hello" },
    ]);
    expect(state).toMatchObject({ phase: "streaming", text: "Hello" });
  });

  it("returns to idle on reset, from any state", () => {
    expect(run([{ type: "submit", question: "q" }, { type: "reset" }])).toEqual(initialStreamState);
    expect(
      streamReducer(
        { phase: "failed", question: "q", error: failure, partial: null },
        { type: "reset" },
      ),
    ).toEqual(initialStreamState);
  });
});
