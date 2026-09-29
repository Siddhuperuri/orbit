"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { CommandPalette } from "@/components/layout/command-palette";
import { FloatingNav } from "@/components/layout/floating-nav";
import { GridLines } from "@/components/layout/grid-lines";
import { SidebarContent } from "@/components/layout/sidebar";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { EmailVerificationBanner } from "@/features/auth/components/email-verification-banner";

/**
 * The authenticated application frame.
 *
 * The navigation floats: a pill over the middle of the page (workspace at the left,
 * search and account at the right), and below `lg` a menu button opens the full
 * navigation as a drawer.
 *
 * The frame is exactly the viewport tall and `<main>` scrolls beneath the floating
 * navbar, so the chrome never moves and a screen like chat can fill the remaining
 * height and manage its own scrolling.
 *
 * Landmarks: `<nav>` (in the floating navbar), `<header>` (the navbar), and a single
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
    <div className="grain bg-canvas relative h-dvh overflow-hidden">
      {/* The column grid runs behind everything, fixed while the page scrolls under it. */}
      <GridLines />

      <FloatingNav
        onOpenNavigation={() => setNavOpen(true)}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent aria-describedby={undefined}>
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Workspace navigation and switcher</SheetDescription>
          <SidebarContent onNavigate={() => setNavOpen(false)} />
        </SheetContent>
      </Sheet>

      {/* The page scrolls beneath the floating navbar; the top padding is its height. */}
      <main
        id="main"
        ref={mainRef}
        tabIndex={-1}
        className="relative flex h-full min-w-0 flex-col overflow-y-auto pt-[5.25rem] sm:pt-[6.25rem]"
      >
        <EmailVerificationBanner />
        <div className="min-h-0 flex-1">{children}</div>
      </main>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
