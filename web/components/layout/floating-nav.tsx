"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoMark } from "@/components/layout/wordmark";
import { NAV_ITEMS } from "@/components/layout/sidebar-nav";
import { SearchTrigger } from "@/components/layout/search-trigger";
import { UserMenu } from "@/components/layout/user-menu";
import { WorkspaceSwitcher } from "@/features/workspaces/components/workspace-switcher";
import { useActiveWorkspace } from "@/features/workspaces/hooks/use-active-workspace";
import { ROLE_LABELS } from "@/features/workspaces/permissions";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

/** How many times the ticker's phrase repeats inside one half of its (duplicated) track. */
const TICKER_REPEATS = 6;

/**
 * The application's chrome, floating over the page: the workspace on the left, the
 * sections in a dithered pill in the middle, and search and the account on the right.
 *
 * The pill is a dark key-like slab (ink on paper, paper on ink) with its sections set
 * as mono labels -- the current one lit -- and a ticker beneath it that carries the
 * workspace's name and your role in it. Below `lg` the pill shrinks to the mark, the
 * current section, and a menu button that opens the full navigation as a drawer.
 *
 * Landmarks: the sections are the one `<nav aria-label="Primary">`; the ticker is
 * decorative (`aria-hidden`) and repeats information the switcher already states.
 */
export function FloatingNav({
  onOpenNavigation,
  onOpenPalette,
}: {
  onOpenNavigation: () => void;
  onOpenPalette: () => void;
}) {
  const pathname = usePathname();
  const { workspace } = useActiveWorkspace();

  const items = workspace
    ? NAV_ITEMS.map((item) => ({
        ...item,
        href: item.href(workspace.id),
        active: item.isActive(pathname, workspace.id),
      }))
    : [];
  const current = items.find((item) => item.active);
  const ticker = workspace
    ? `${workspace.name} · ${workspace.role ? ROLE_LABELS[workspace.role] : "Member"}`
    : "ORBIT";

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-40 grid grid-cols-[1fr_auto_1fr] items-start gap-3 p-3 sm:p-4">
      <div className="pointer-events-auto hidden w-64 lg:block">
        <div className="border-line bg-surface/85 shadow-card rounded-2xl border backdrop-blur-md">
          <WorkspaceSwitcher />
        </div>
      </div>
      <div className="lg:hidden" />

      <div className="pointer-events-auto col-start-2">
        <div className="dither rounded-[1.4rem] p-[3px]">
          <div className="bg-accent-solid text-on-accent shadow-float overflow-hidden rounded-[1.15rem]">
            <nav aria-label="Primary" className="flex items-center gap-1 p-1.5">
              <Link
                href={routes.home}
                aria-label="ORBIT home"
                className="group hover:bg-on-accent/10 flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors"
              >
                <LogoMark className="size-5" />
              </Link>

              {items.map((item) => (
                <Link
                  key={item.label}
                  href={item.href}
                  aria-current={item.active ? "page" : undefined}
                  className={cn(
                    "label-caps hidden h-9 items-center rounded-lg px-3 transition-colors duration-200 lg:inline-flex",
                    item.active
                      ? "bg-on-accent/20 text-on-accent"
                      : "text-on-accent/75 hover:bg-on-accent/10 hover:text-on-accent",
                  )}
                >
                  {item.label}
                </Link>
              ))}

              {/* Small screens: the current place, and the door to everything else. */}
              <span className="label-caps text-on-accent/90 px-2 lg:hidden">
                {current?.label ?? "Menu"}
              </span>
              <button
                type="button"
                onClick={onOpenNavigation}
                aria-label="Open navigation"
                className="hover:bg-on-accent/10 flex size-9 items-center justify-center rounded-lg transition-colors lg:hidden"
              >
                <Menu className="size-4" aria-hidden="true" />
              </button>
            </nav>

            <div
              aria-hidden="true"
              className="bg-canvas text-fg mx-1.5 mb-1.5 hidden overflow-hidden rounded-md py-1 [contain:inline-size] sm:block"
            >
              <div className="marquee label-micro">
                {[0, 1].map((half) => (
                  <div key={half} className="flex shrink-0">
                    {Array.from({ length: TICKER_REPEATS }, (_, index) => (
                      <span key={index} className="shrink-0 px-4 whitespace-nowrap">
                        {ticker}
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="pointer-events-auto col-start-3 flex items-center justify-end gap-2">
        <SearchTrigger onOpen={onOpenPalette} />
        <UserMenu />
      </div>
    </header>
  );
}
