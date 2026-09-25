/** A conversation with no messages yet. Says what to do, and what to expect back. */
export function EmptyThread() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h2 className="text-fg text-lg font-semibold">Ask about your documents</h2>
      <p className="text-fg-muted mt-1.5 text-base">
        Answers come only from what you&apos;ve uploaded, and every claim links to the passage it
        came from. If your documents don&apos;t cover something, you&apos;ll be told rather than
        given a guess.
      </p>
    </div>
  );
}
