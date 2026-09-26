"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { SearchTrigger } from "@/components/layout/search-trigger";
import { UserMenu } from "@/components/layout/user-menu";
import { Button } from "@/components/ui/button";
import { useActiveWorkspace } from "@/features/workspaces/hooks/use-active-workspace";
import { routes } from "@/lib/navigation";

/** The section a path belongs to, for the breadcrumb. */
function sectionOf(pathname: string, workspaceId: string | undefined): string | null {
  if (pathname.startsWith(routes.account)) return "Account";
  if (pathname.startsWith(routes.newWorkspace)) return "New workspace";
  if (!workspaceId) return null;
  const base = routes.workspace(workspaceId);
  if (pathname.startsWith(`${base}/documents`)) return "Documents";
  if (pathname.startsWith(`${base}/search`)) return "Search";
  if (pathname.startsWith(`${base}/chat`)) return "Chat";
  if (pathname.startsWith(`${base}/settings/members`)) return "Members";
  if (pathname.startsWith(`${base}/settings`)) return "Settings";
  return null;
}

/**
 * The bar across the top of the content panel: navigation toggle (below `lg`,
 * where the sidebar is a drawer), where-you-are breadcrumb, the command palette
 * entry point, and -- where the sidebar is hidden -- the account menu.
 */
export function Topbar({
  onOpenNavigation,
  onOpenPalette,
}: {
  onOpenNavigation: () => void;
  onOpenPalette: () => void;
}) {
  const pathname = usePathname();
  const { workspace, routeWorkspaceId } = useActiveWorkspace();
  // Only a page inside the workspace belongs under it in the breadcrumb.
  const crumbWorkspace = routeWorkspaceId ? workspace : undefined;
  const section = sectionOf(pathname, routeWorkspaceId);

  return (
    <header className="border-line relative flex h-14 shrink-0 items-center gap-2 border-b px-3 sm:px-6 lg:px-10 pointer-coarse:h-16">
      <Button
        variant="ghost"
        size="icon"
        className="-ml-1 lg:hidden"
        onClick={onOpenNavigation}
        aria-label="Open navigation"
      >
        <Menu aria-hidden="true" />
      </Button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="label-micro flex items-center gap-2.5">
          {crumbWorkspace ? (
            <li className="hidden min-w-0 items-center gap-2 sm:flex">
              <Link
                href={routes.documents(crumbWorkspace.id)}
                className="text-fg-subtle hover:text-fg max-w-56 truncate rounded-xs transition-colors"
              >
                {crumbWorkspace.name}
              </Link>
              {section ? (
                <span className="text-line-strong" aria-hidden="true">
                  —
                </span>
              ) : null}
            </li>
          ) : null}
          {section ? (
            <li className="text-fg truncate" aria-current="page">
              {section}
            </li>
          ) : null}
        </ol>
      </nav>

      <SearchTrigger onOpen={onOpenPalette} />
      <div className="lg:hidden">
        <UserMenu />
      </div>
    </header>
  );
}
