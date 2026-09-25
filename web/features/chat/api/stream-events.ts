import { z } from "zod";

import type { Message } from "@/features/chat/types";

/**
 * Runtime schemas for the answer stream's event payloads.
 *
 * OpenAPI cannot describe Server-Sent Events, so unlike every other response these
 * three shapes are absent from the generated types. They are the one place the
 * frontend describes a backend payload itself -- which is precisely why they are
 * *validated* rather than merely cast: a frame that does not match is reported as a
 * failed answer instead of being trusted and crashing a component later.
 *
 * `done` is the exception in the other direction: it carries a full message, whose
 * shape *is* generated (`MessageOut`), so it is only checked for the fields the
 * client cannot function without.
 */

export const startedEventSchema = z.object({
  conversation_id: z.string(),
  question_message_id: z.string(),
  answer_message_id: z.string(),
});

export const retrievalEventSchema = z.object({
  retrieved: z.number(),
  sources: z.number(),
  degraded: z.string().nullable(),
  retrieval_ms: z.number(),
});

export const deltaEventSchema = z.object({ text: z.string() });

/** A finished message. Full validation is the generated type's job; this guards the essentials. */
const messageSchema = z.custom<Message>(
  (value) =>
    typeof value === "object" &&
    value !== null &&
    typeof (value as { id?: unknown }).id === "string" &&
    typeof (value as { content?: unknown }).content === "string",
  "Not a message",
);

export const errorEventSchema = z.object({
  code: z.string(),
  message: z.string(),
  request_id: z.string(),
  retryable: z.boolean(),
  retry_after_seconds: z.number().nullish(),
  /** The answer as recorded -- possibly partial -- if it could be recorded. */
  answer: messageSchema.nullish(),
});

export type StartedEvent = z.infer<typeof startedEventSchema>;
export type RetrievalEvent = z.infer<typeof retrievalEventSchema>;
export type ErrorEvent = z.infer<typeof errorEventSchema>;

export const EVENT_SCHEMAS = {
  started: startedEventSchema,
  retrieval: retrievalEventSchema,
  delta: deltaEventSchema,
  done: messageSchema,
  error: errorEventSchema,
} as const;

export type EventName = keyof typeof EVENT_SCHEMAS;

export function isEventName(name: string): name is EventName {
  return name in EVENT_SCHEMAS;
}
