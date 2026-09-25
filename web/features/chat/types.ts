import type { Schema } from "@/lib/api/types";

export type Conversation = Schema<"ConversationOut">;
export type Message = Schema<"MessageOut">;
export type Citation = Schema<"CitationOut">;
export type Grounding = Schema<"Grounding">;
export type MessageStatus = Schema<"MessageStatus">;
export type StopReason = Schema<"StopReason">;

export type { ErrorEvent, RetrievalEvent, StartedEvent } from "@/features/chat/api/stream-events";

/** The API's limit on one question, mirrored so the composer can state it. */
export const MAX_QUESTION_CHARACTERS = 2000;
/** The API's limit on how many documents one question can be scoped to. */
export const MAX_SCOPED_DOCUMENTS = 100;
