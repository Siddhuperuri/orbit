import { Compass } from "lucide-react";
import Link from "next/link";

import { AsciiVortex } from "@/components/ascii/ascii-vortex";
import { OrbitIcon } from "@/components/feedback/empty-state";
import { GridLines } from "@/components/layout/grid-lines";
import { Wordmark } from "@/components/layout/wordmark";
import { KineticText } from "@/components/ui/kinetic-text";
import { Button } from "@/components/ui/button";
import { routes } from "@/lib/navigation";

export default function NotFound() {
  return (
    <div className="grain bg-canvas relative flex min-h-dvh flex-col overflow-hidden">
      <GridLines />
      <AsciiVortex
        phrase="PAGE NOT FOUND · OUT OF ORBIT · "
        className="absolute inset-0 [mask-image:radial-gradient(ellipse_45%_42%_at_50%_50%,transparent_55%,#000_100%)]"
      />
      <header className="relative px-4 py-5 sm:px-6 lg:px-10">
        <Link href={routes.home} className="inline-flex rounded-md" aria-label="ORBIT home">
          <Wordmark />
        </Link>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="@container relative mx-auto flex w-full max-w-3xl flex-1 flex-col items-center justify-center px-4 pb-24 text-center"
      >
        <OrbitIcon icon={Compass} className="enter mb-8" />
        <p className="enter enter-1 label-micro text-fg-subtle">Error 404</p>
        <h1 className="text-fg mt-4 text-[clamp(3.5rem,1rem+12cqi,8rem)] leading-[0.9] font-semibold tracking-[-0.04em]">
          <KineticText text="Out of orbit" />
        </h1>
        <p className="enter enter-2 text-fg-muted mt-6 text-base">
          The page you followed doesn&apos;t exist, or you may not have access to it.
        </p>
        <Button asChild variant="primary" size="lg" className="enter enter-3 mt-10">
          <Link href={routes.home}>Go to ORBIT home</Link>
        </Button>
      </main>
    </div>
  );
}
