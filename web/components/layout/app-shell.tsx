"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { CommandPalette } from "@/components/layout/command-palette";
import { GridLines } from "@/components/layout/grid-lines";
import { SidebarContent } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { EmailVerificationBanner } from "@/features/auth/components/email-verification-banner";

/**
 * The authenticated application frame.
 *
 *   >= lg   persistent sidebar (16rem) ruled off from the working column
 *   <  lg   the sidebar becomes a drawer opened from the top bar
 *
 * The frame is exactly the viewport tall and `<main>` scrolls inside the column,
 * so the sidebar and top bar never move and a screen like chat can fill the
 * remaining height and manage its own scrolling.
 *
 * Landmarks: `<nav>` (inside the sidebar), `<header>` (top bar), and a single
 * `<main id="main">`, which the skip link targets and which is focused after every
 * client-side navigation -- without that, a keyboard or screen-reader user is left
 * on a link in a page that has since changed.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pathname = usePathname();
  const mainRef = useRef<HTMLElement>(null);
  const lastPathname = useRef(pathname);

  // Ctrl/Cmd+K opens the palette from anywhere. A modified shortcut, so it does
  // not collide with typing and needs no way to be turned off (WCAG 2.1.4).
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Move focus to the new page's content after navigation (not on first load,
  // where the browser's own focus handling is correct).
  useEffect(() => {
    // Compared with the last path rather than tracked with a "first render" flag:
    // React runs effects twice in development, which defeats a flag and would
    // focus <main> on page load -- making the first Tab skip the skip link, the
    // sidebar, and the top bar.
    if (lastPathname.current === pathname) return;
    lastPathname.current = pathname;
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="grain bg-canvas flex h-dvh overflow-hidden">
      <aside className="border-line hidden h-full w-64 shrink-0 border-r lg:block">
        <SidebarContent />
      </aside>

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent aria-describedby={undefined}>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Workspace navigation and switcher</SheetDescription>
          <SidebarContent onNavigate={() => setNavOpen(false)} />
        </SheetContent>
      </Sheet>

      {/* The working column: one black field, ruled by the column grid, which runs
          behind the top bar and the page alike. */}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        <GridLines />
        <Topbar
          onOpenNavigation={() => setNavOpen(true)}
          onOpenPalette={() => setPaletteOpen(true)}
        />
        <EmailVerificationBanner />
        <main
          id="main"
          ref={mainRef}
          tabIndex={-1}
          className="relative min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          {children}
        </main>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
