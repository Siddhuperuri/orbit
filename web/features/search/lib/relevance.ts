import type { SearchMode, SearchResult } from "@/features/search/types";

/**
 * What to tell a reader about *why* a passage is in their results.
 *
 * The API returns the ranking's internals -- each retriever's position and native
 * score, and the fused RRF score. None of that is shown. A fused score of `0.0164`
 * is meaningless outside the one response that produced it; `ts_rank_cd` and cosine
 * similarity live on different scales and are not comparable with each other or
 * across queries. Putting those numbers on screen would invite the reader to
 * compare things that cannot be compared, and to believe a precision the ranking
 * does not have.
 *
 * What *does* have product value is the fact a reader can act on: whether this
 * passage matched the words they typed, the meaning behind them, or both -- because
 * that tells them how to change the query. "Matched on meaning, not your exact
 * words" is a reason to try the exact phrase; "keyword only" is a reason to
 * rephrase. That is what this module produces.
 */

export interface RelevanceExplanation {
  /** A short label for the result header. */
  label: string;
  /** One sentence, for the "Why this result" disclosure. */
  detail: string;
}

const HEADLINE: Record<SearchResult["matched_by"], string> = {
  both: "Strong match",
  lexical: "Keyword match",
  semantic: "Meaning match",
};

const WHY: Record<SearchResult["matched_by"], string> = {
  both: "This passage uses the words you typed and is about what you asked. Matches found both ways rank highest.",
  lexical:
    "This passage contains the words you typed. It may not be the closest in meaning -- try describing what you want instead of naming it.",
  semantic:
    "This passage is about what you asked, but doesn't use your exact words. Try the exact phrase if you're looking for a specific term.",
};

export function explainRelevance(result: SearchResult): RelevanceExplanation {
  return { label: HEADLINE[result.matched_by], detail: WHY[result.matched_by] };
}

/**
 * Whether the "matched by" label is worth showing at all.
 *
 * In a single-retriever search every result matched the same single way, so the
 * badge would repeat down the page carrying no information. It earns its place
 * only in hybrid mode, where results genuinely differ.
 */
export function shouldExplainMatch(mode: SearchMode): boolean {
  return mode === "hybrid";
}
