import type { Citation, Message } from "@/features/chat/types";

/**
 * The API omits fields that have defaults (an empty `citations` list), and the
 * generated types faithfully mark them optional. Components read through here so
 * the "absent" case is handled once, not at every use.
 */
export function citationsOf(message: Message): readonly Citation[] {
  return message.citations ?? [];
}
