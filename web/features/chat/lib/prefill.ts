/**
 * Hands a question from the command palette to the composer, in memory.
 *
 * A question is user content, and the API deliberately keeps it out of URLs (it
 * uses `POST` for search and answers so queries never reach access logs, browser
 * history, or a `Referer`). Passing it as `?q=` to the chat page would undo that,
 * so it travels through this variable instead: the palette sets it, the composer
 * reads it as the field's starting text, and it is cleared once the composer is
 * done with it.
 *
 * The composer *pre-fills* rather than auto-sends. Sending is an action with cost
 * (retrieval plus a language-model call) and should follow an intentional Enter.
 */

let prefilled: string | null = null;

export function setPrefilledQuestion(question: string): void {
  prefilled = question;
}

/** Reads without consuming: React may render a component twice in development. */
export function peekPrefilledQuestion(): string {
  return prefilled ?? "";
}

export function clearPrefilledQuestion(): void {
  prefilled = null;
}
