/**
 * A conversation's title, taken from the question that started it.
 *
 * The API accepts a title when a conversation is created but never invents one, so
 * without this every conversation in the list would read "New conversation" and
 * the list would be useless past the first. Only the first line is used, whitespace
 * is collapsed, and the result is cut on a word boundary with an ellipsis.
 */

/** Long enough to be recognisable in a 280px list, well within the API's 512-character limit. */
const MAX_TITLE_LENGTH = 72;

export function titleFromQuestion(question: string): string | undefined {
  const firstLine = question.split(/\r?\n/).find((line) => line.trim() !== "") ?? "";
  const collapsed = firstLine.replace(/\s+/g, " ").trim();
  if (collapsed === "") return undefined;
  if (collapsed.length <= MAX_TITLE_LENGTH) return collapsed;

  const cut = collapsed.slice(0, MAX_TITLE_LENGTH);
  const lastSpace = cut.lastIndexOf(" ");
  // Cut at a word boundary unless that would throw away most of the title.
  const base = lastSpace > MAX_TITLE_LENGTH * 0.6 ? cut.slice(0, lastSpace) : cut;
  return `${base.replace(/[\s.,;:!?-]+$/, "")}…`;
}
