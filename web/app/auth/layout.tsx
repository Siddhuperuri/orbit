import { FileSearch, Quote, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { AsciiVortex } from "@/components/ascii/ascii-vortex";
import { GridLines } from "@/components/layout/grid-lines";
import { Wordmark } from "@/components/layout/wordmark";
import { routes } from "@/lib/navigation";

/** The spiral's eye: high and to the right, so its arms sweep down behind the copy. */
const CENTER = [0.66, 0.17] as const;

const POINTS = [
  { icon: FileSearch, text: "Search every passage of every document at once." },
  { icon: Quote, text: "Answers cite the exact passage behind each claim." },
  { icon: ShieldCheck, text: "When your documents don't say, ORBIT says so." },
];

/**
 * A picture of the product's one promise: an answer, and the passage it came from,
 * marked the way a reader would mark it. Illustrative copy, not user data. It floats
 * on glass in front of the artwork.
 */
function CitationPreview() {
  return (
    <figure className="float-slow bg-surface/70 border-line-strong shadow-float max-w-md rounded-2xl border p-6 backdrop-blur-xl">
      <p className="text-fg text-lg leading-relaxed font-medium tracking-tight">
        Remote staff may claim home-office equipment up to the annual allowance, provided purchases
        are approved in advance
        <span className="bg-accent-soft text-accent-soft-fg text-2xs mx-1 inline-flex h-5 items-center px-1 align-[0.12em] font-mono font-medium">
          S1
        </span>
        .
      </p>
      <figcaption className="border-line mt-5 border-t pt-4">
        <p className="label-micro text-fg-subtle mb-2 flex items-center gap-2">
          <span className="text-accent">S1</span>
          Employee Handbook · page 14
        </p>
        <p className="text-fg-muted text-base leading-relaxed">
          …employees working remotely{" "}
          <mark className="px-0.5">
            may be reimbursed for home-office equipment up to the annual allowance
          </mark>
          , subject to prior approval by their manager…
        </p>
      </figcaption>
    </figure>
  );
}

/**
 * Shared frame for the sign-in family of pages, laid out like a product's front door:
 * the form on paper at the left, and at the right a charcoal panel where ORBIT's own
 * words spiral outward as ASCII (a canvas, turning slowly, bending toward the pointer),
 * with the promise set over it -- the statement, the three things it does, and proof
 * (an answer beside the passage it came from).
 *
 * The panel is always the night palette (its own `data-theme`), so it reads the same in
 * either theme; on a phone it is dropped, and the form is the whole page.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grain bg-canvas flex min-h-dvh">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <GridLines />
        <header className="relative px-6 py-6 sm:px-10">
          <Link href={routes.home} className="inline-flex rounded-md" aria-label="ORBIT home">
            <Wordmark />
          </Link>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className="relative mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 pt-4 pb-24 lg:max-w-md lg:px-10"
        >
          {children}
        </main>
        <p className="label-micro text-fg-subtle relative hidden px-10 pb-8 lg:block">
          Upload PDFs, Markdown and text · Search · Ask with citations
          <span className="caret-block" aria-hidden="true" />
        </p>
      </div>

      <aside
        data-theme="dark"
        aria-label="About ORBIT"
        className="bg-canvas text-fg @container relative hidden min-h-dvh w-[52%] max-w-[64rem] shrink-0 flex-col justify-between overflow-hidden lg:flex"
      >
        <AsciiVortex
          center={CENTER}
          className="absolute inset-0 [mask-image:linear-gradient(to_bottom,#000_16%,transparent_47%)]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(85%_70%_at_10%_100%,var(--canvas)_38%,transparent_78%),linear-gradient(to_top,var(--canvas)_18%,transparent_55%)]"
        />

        <div className="relative flex justify-end p-8 xl:p-10">
          <span className="label-micro border-line-strong bg-surface/70 text-fg-muted rounded-md border px-2.5 py-1.5 backdrop-blur-md">
            ORBIT
          </span>
        </div>

        <div className="relative max-w-2xl space-y-8 p-8 xl:p-12">
          <h2 className="text-[clamp(2.5rem,5.6cqi,4.25rem)] leading-[1.02] font-semibold tracking-[-0.04em] text-balance">
            <span className="kinetic-word">
              <span>Ask your documents.</span>
            </span>
            <br />
            <span className="kinetic-word">
              <span className="text-fg-muted">Check every answer.</span>
            </span>
          </h2>
          <ul className="enter enter-2 divide-line max-w-md divide-y">
            {POINTS.map(({ icon: Icon, text }) => (
              <li key={text} className="text-fg-muted flex items-center gap-4 py-3 text-sm">
                <span className="border-line-strong bg-surface/60 text-accent inline-flex size-8 shrink-0 items-center justify-center rounded-lg border backdrop-blur-md">
                  <Icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </span>
                {text}
              </li>
            ))}
          </ul>
          <div className="enter enter-3">
            <CitationPreview />
          </div>
        </div>
      </aside>
    </div>
  );
}
