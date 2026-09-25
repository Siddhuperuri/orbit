import Link from "next/link";

import { Wordmark } from "@/components/layout/wordmark";
import { Button } from "@/components/ui/button";
import { routes } from "@/lib/navigation";

export default function NotFound() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="px-6 py-5">
        <Wordmark />
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto flex w-full max-w-md flex-1 flex-col items-center justify-center px-4 pb-24 text-center"
      >
        <p className="text-fg-muted font-mono text-sm">404</p>
        <h1 className="text-fg mt-2 text-xl font-semibold">Page not found</h1>
        <p className="text-fg-muted mt-1.5 text-base">
          The page you followed doesn&apos;t exist, or you may not have access to it.
        </p>
        <Button asChild variant="primary" className="mt-6">
          <Link href={routes.home}>Go to ORBIT home</Link>
        </Button>
      </main>
    </div>
  );
}
