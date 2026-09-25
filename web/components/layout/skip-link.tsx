/**
 * The first focusable element on every page. It is invisible until focused, then
 * lets a keyboard user jump past the navigation straight to the content -- without
 * it, every page load means tabbing through the whole sidebar (WCAG 2.4.1).
 */
export function SkipLink() {
  return (
    <a
      href="#main"
      className="focus:border-line-strong focus:bg-surface focus:text-fg focus:shadow-float sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100] focus:rounded-md focus:border focus:px-3 focus:py-2 focus:text-base focus:font-medium"
    >
      Skip to main content
    </a>
  );
}
