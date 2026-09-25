"use client";

import "@fontsource-variable/ibm-plex-sans";
import "@/app/globals.css";

/**
 * The last resort: it replaces the root layout, so it brings its own `<html>` and
 * `<body>`. It runs when the layout itself fails, which means nothing else (the
 * providers, the theme script, the shell) can be assumed -- so it depends on
 * nothing and simply offers a reload.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <main id="main" role="alert" className="mx-auto max-w-md px-4 py-32 text-center">
          <h1 className="text-lg font-semibold">ORBIT hit an unexpected problem</h1>
          <p className="mt-2 text-base">
            Reload the page to try again. If it keeps happening, quote the reference below to
            support.
          </p>
          {error.digest ? <p className="mt-3 font-mono text-xs">Reference {error.digest}</p> : null}
          <button
            type="button"
            onClick={reset}
            className="bg-accent-solid text-on-accent mt-6 h-10 rounded-md px-4 text-base font-medium"
          >
            Reload
          </button>
        </main>
      </body>
    </html>
  );
}
