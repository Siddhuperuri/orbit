/**
 * Text whose words rise one after another out of their own masks when it first
 * appears -- the title-card entrance used for page headings.
 *
 * Each word is wrapped in two spans (a clipping mask, and the moving word), and the
 * spaces between them stay real text nodes, so the accessible name and copied text
 * are exactly the original string. Timing lives in `globals.css` (`.kinetic-word`),
 * staggered by position; with reduced motion the words are simply there.
 */
export function KineticText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\s+)/).map((part, index) =>
        part === "" ? null : /^\s+$/.test(part) ? (
          part
        ) : (
          <span key={index} className="kinetic-word">
            <span>{part}</span>
          </span>
        ),
      )}
    </>
  );
}
