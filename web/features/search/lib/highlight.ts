/**
 * Splits text into segments so the words a query matched can be emphasised.
 *
 * Returns *segments*, not HTML: the caller renders each as a React text node (or
 * a `<mark>` around one), so document text is never interpolated into markup. A
 * document is untrusted input -- this is one of the places stored XSS would enter
 * if it were rendered as a string of HTML, and the reason the lint config forbids
 * `dangerouslySetInnerHTML`.
 */

export interface Segment {
  text: string;
  match: boolean;
}

/** Words shorter than this match too much of any text to be useful as emphasis. */
const MIN_TERM_LENGTH = 2;

const MAX_TERMS = 12;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function queryTerms(query: string): string[] {
  const unique = new Set<string>();
  for (const raw of query.split(/\s+/)) {
    // Strip punctuation the user typed around a word ("quarterly," or "(budget)").
    const term = raw.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "").toLowerCase();
    if (term.length >= MIN_TERM_LENGTH) unique.add(term);
    if (unique.size >= MAX_TERMS) break;
  }
  return [...unique];
}

export function highlightSegments(text: string, query: string): Segment[] {
  const terms = queryTerms(query);
  if (terms.length === 0 || text === "") return [{ text, match: false }];

  // Longest first, so "database" wins over "data" when both are terms.
  const pattern = new RegExp(
    `(${terms
      .sort((a, b) => b.length - a.length)
      .map(escapeRegExp)
      .join("|")})`,
    "giu",
  );

  const segments: Segment[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > lastIndex) segments.push({ text: text.slice(lastIndex, start), match: false });
    segments.push({ text: match[0], match: true });
    lastIndex = start + match[0].length;
  }
  if (lastIndex < text.length) segments.push({ text: text.slice(lastIndex), match: false });

  return segments;
}
