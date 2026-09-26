import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";

import type { Metadata, Viewport } from "next";
import Script from "next/script";

import { Providers } from "@/app/providers";
import { SkipLink } from "@/components/layout/skip-link";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: {
    default: "ORBIT",
    template: "%s · ORBIT",
  },
  description: "Intelligent knowledge and document platform.",
  // Every page is behind authentication and holds private documents, so none of
  // it should ever be indexed.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Zoom is not disabled: preventing it fails WCAG 1.4.4 and breaks the app for
  // anyone who needs to magnify text.
  colorScheme: "light dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning`: theme-init.js sets `data-theme` on <html> before
    // React hydrates, which is exactly the mismatch it would otherwise warn about.
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* Blocks first paint for a few bytes so a dark-mode user never sees the
            light palette flash. A same-origin file, so no inline script (and no
            CSP `unsafe-inline`) is needed. */}
        <Script src="/theme-init.js" strategy="beforeInteractive" />
        <SkipLink />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
