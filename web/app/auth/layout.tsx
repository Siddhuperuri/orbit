import { FileSearch, Quote, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { GridLines } from "@/components/layout/grid-lines";
import { OrbitArt } from "@/components/layout/orbit-art";
import { Wordmark } from "@/components/layout/wordmark";
import { routes } from "@/lib/navigation";

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
    <figure className="float-slow bg-surface/55 border-line-strong max-w-md border p-6 backdrop-blur-xl">
      <p className="text-fg font-serif text-lg leading-relaxed">
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
        <p className="text-fg-muted font-serif text-base leading-relaxed">
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
 * Shared frame for the sign-in family of pages. On a wide screen the form sits
 * beside a brand panel that says what ORBIT is for -- laid out the way a product's
 * front door usually is: the name at the top, one large statement, the three
 * things it promises, and proof of the promise (an answer beside the passage it came
 * from) at the foot. A soft cobalt glow and the slowly turning orbit artwork give
 * it depth without competing with the text.
 *
 * The panel is always the night palette (its own `data-theme`), so it reads the same
 * in either theme; on a phone it is dropped, and the form is the whole page.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grain bg-canvas flex min-h-dvh">
      <aside
        data-theme="dark"
        aria-label="About ORBIT"
        className="bg-canvas text-fg @container relative hidden min-h-dvh w-1/2 max-w-[60rem] shrink-0 flex-col justify-between gap-12 overflow-hidden p-10 lg:flex xl:p-14"
      >
        {/* Backdrop: a cobalt glow from the upper right, the column grid, a floor of black. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_82%_30%,color-mix(in_oklch,var(--accent)_20%,transparent),transparent_70%)]"
        />
        <GridLines />
        <OrbitArt className="scroll-depth absolute top-1/2 right-[2%] size-[54cqi] -translate-y-[58%] opacity-80" />
        <div
          aria-hidden="true"
          className="from-canvas pointer-events-none absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t to-transparent"
        />

        <Link
          href={routes.home}
          aria-label="ORBIT home"
          className="group enter relative inline-flex self-start"
        >
          <Wordmark />
        </Link>

        <div className="relative max-w-xl space-y-10">
          <h2 className="font-serif text-[clamp(2.75rem,6.4cqi,4.75rem)] leading-[0.98] font-light tracking-[-0.035em] text-balance">
            <span className="kinetic-word">
              <span>Ask your documents.</span>
            </span>
            <br />
            <span className="kinetic-word">
              <span className="text-fg-muted italic">Check every answer.</span>
            </span>
          </h2>
          <ul className="enter enter-2 border-line divide-line max-w-md divide-y border-y">
            {POINTS.map(({ icon: Icon, text }) => (
              <li key={text} className="text-fg-muted flex items-center gap-4 py-3.5 text-sm">
                <span className="border-line-strong text-accent inline-flex size-8 shrink-0 items-center justify-center border">
                  <Icon className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>

        <div className="enter enter-3 relative space-y-5">
          <CitationPreview />
          <p className="label-micro text-fg-subtle">
            Upload PDFs, Markdown and text · Search · Ask with citations
          </p>
        </div>
      </aside>

      <div className="border-line bg-surface relative flex min-w-0 flex-1 flex-col lg:border-l">
        <header className="px-6 py-5 lg:hidden">
          <Link href={routes.home} className="inline-flex rounded-md" aria-label="ORBIT home">
            <Wordmark />
          </Link>
        </header>
        <main
          id="main"
          tabIndex={-1}
          className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 pt-6 pb-24 lg:max-w-md lg:px-10 lg:pb-12"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
