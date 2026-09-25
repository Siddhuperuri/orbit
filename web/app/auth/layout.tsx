import Link from "next/link";

import { Wordmark } from "@/components/layout/wordmark";
import { routes } from "@/lib/navigation";

/**
 * Shared frame for the sign-in family of pages: the wordmark, and a single narrow
 * column. No marketing panel -- someone arriving here wants to get into a tool.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="px-6 py-5">
        <Link href={routes.home} className="inline-flex rounded-md" aria-label="ORBIT home">
          <Wordmark />
        </Link>
      </header>
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 pb-24"
      >
        {children}
      </main>
    </div>
  );
}
