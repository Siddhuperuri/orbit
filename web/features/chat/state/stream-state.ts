import type { ErrorEvent, Message } from "@/features/chat/types";

/**
 * The lifecycle of one streamed answer, as a pure state machine.
 *
 * Kept free of React and of I/O so its transitions -- which are where streaming
 * UIs go wrong (a late delta after an error, a `done` that never arrives, a retry
 * mid-stream) -- can be tested exhaustively.
 *
 *   idle -> retrieving -> streaming -> settled      (the answer is a saved message)
 *                  \           \-----> failed        (with whatever was recorded)
 *                   \--------------> failed
 *   any -> idle on reset or a user's stop
 */

export type StreamState =
  | { phase: "idle" }
  | { phase: "retrieving"; question: string }
  | {
      phase: "streaming";
      question: string;
      text: string;
      retrieved: number;
      sources: number;
      degraded: string | null;
    }
  | { phase: "failed"; question: string; error: ErrorEvent; partial: Message | null }
  | { phase: "settled" };

export type StreamAction =
  | { type: "submit"; question: string }
  | { type: "retrieval"; retrieved: number; sources: number; degraded: string | null }
  | { type: "delta"; text: string }
  | { type: "done"; message: Message }
  | { type: "error"; error: ErrorEvent }
  | { type: "reset" };

export const initialStreamState: StreamState = { phase: "idle" };

export type BusyState = Extract<StreamState, { phase: "retrieving" | "streaming" }>;

/** A type predicate, so a caller that has checked it can read `question` without a cast. */
export function isBusy(state: StreamState): state is BusyState {
  return state.phase === "retrieving" || state.phase === "streaming";
}

export function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "submit":
      // A new question while one is in flight is refused by the API (409); the UI
      // prevents it, and the reducer agrees rather than trusting the UI.
      return isBusy(state) ? state : { phase: "retrieving", question: action.question };

    case "retrieval":
      if (state.phase !== "retrieving") return state;
      return {
        phase: "streaming",
        question: state.question,
        text: "",
        retrieved: action.retrieved,
        sources: action.sources,
        degraded: action.degraded,
      };

    case "delta":
      // Deltas can arrive without a `retrieval` event having been seen (or after a
      // reconnect): start streaming rather than dropping the text.
      if (state.phase === "retrieving") {
        return {
          phase: "streaming",
          question: state.question,
          text: action.text,
          retrieved: 0,
          sources: 0,
          degraded: null,
        };
      }
      if (state.phase !== "streaming") return state;
      return { ...state, text: state.text + action.text };

    case "done":
      // The finished message is authoritative; it replaces everything streamed.
      return isBusy(state) ? { phase: "settled" } : state;

    case "error":
      if (!isBusy(state)) return state;
      return {
        phase: "failed",
        question: state.question,
        error: action.error,
        partial: action.error.answer ?? null,
      };

    case "reset":
      return initialStreamState;
  }
}
