import { FileSearch, Quote, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { GridLines } from "@/components/layout/grid-lines";
import { OrbitArt } from "@/components/layout/orbit-art";
import { LogoMark, Wordmark } from "@/components/layout/wordmark";
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

/** The five letters of the name, each rising out of its own mask in turn. */
const LETTERS = ["O", "R", "B", "I", "T"];

/**
 * Shared frame for the sign-in family of pages. On a wide screen the form sits
 * beside a panel that shows what ORBIT is for, composed like a title sequence: the
 * name set across the full width and cut off by the top edge, the column grid, the
 * orbit artwork turning slowly behind, and the product's promise in front. The
 * panel is always the night palette (its own `data-theme`), so it reads the same in
 * either theme; on a phone it is dropped, and the form is the whole page.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grain bg-canvas flex min-h-dvh">
      <aside
        data-theme="dark"
        aria-label="About ORBIT"
        className="bg-canvas text-fg @container relative hidden min-h-dvh w-[56%] max-w-[64rem] shrink-0 flex-col overflow-hidden lg:flex"
      >
        <GridLines />

        <Link
          href={routes.home}
          aria-label="ORBIT home"
          className="group relative block px-4 sm:px-6 lg:px-10"
        >
          {/* The name, set to the panel's width and cut by its top edge. */}
          <span
            aria-hidden="true"
            className="-mt-[0.16em] flex justify-between font-serif text-[24cqi] leading-[0.8] font-extralight tracking-[-0.02em]"
          >
            {LETTERS.map((letter) => (
              <span key={letter} className="kinetic-word">
                <span>{letter}</span>
              </span>
            ))}
          </span>
        </Link>

        <OrbitArt className="scroll-depth absolute top-[8%] -right-[12%] size-[56cqi] opacity-95" />

        <div className="relative mt-auto grid grid-cols-6 px-4 pb-10 sm:px-6 lg:px-10 xl:pb-14">
          <div className="col-span-6 space-y-10 xl:col-span-5">
            <div className="space-y-6">
              <LogoMark className="enter text-fg size-7" />
              <h2 className="font-serif text-[6.2cqi] leading-[0.95] font-light tracking-[-0.035em] text-balance">
                <span className="kinetic-word">
                  <span>Ask your documents.</span>
                </span>
                <br />
                <span className="kinetic-word">
                  <span className="text-fg-muted italic">Check every answer.</span>
                </span>
              </h2>
              <ul className="enter enter-2 border-line grid gap-px border-t pt-5 sm:grid-cols-3 sm:gap-6">
                {POINTS.map(({ icon: Icon, text }) => (
                  <li key={text} className="text-fg-muted flex items-start gap-3 text-sm">
                    <span className="border-line-strong text-accent inline-flex size-7 shrink-0 items-center justify-center border">
                      <Icon className="size-3.5" strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    {text}
                  </li>
                ))}
              </ul>
            </div>
            <div className="enter enter-3">
              <CitationPreview />
            </div>
            <p className="label-micro text-fg-subtle">
              Upload PDFs, Markdown and text · Search · Ask with citations
            </p>
          </div>
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
