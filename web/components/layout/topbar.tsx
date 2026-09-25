"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

import { SearchTrigger } from "@/components/layout/search-trigger";
import { UserMenu } from "@/components/layout/user-menu";
import { Button } from "@/components/ui/button";
import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
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
  if (pathname.startsWith(`${base}/settings`)) return "Settings";
  return null;
}

/**
 * The bar above the content: navigation toggle (below `lg`, where the sidebar is
 * a drawer), where-you-are breadcrumb, the command palette entry point, and the
 * account menu.
 */
export function Topbar({
  onOpenNavigation,
  onOpenPalette,
}: {
  onOpenNavigation: () => void;
  onOpenPalette: () => void;
}) {
  const pathname = usePathname();
  const { workspaceId } = useParams<{ workspaceId?: string }>();
  const { data: workspaces } = useWorkspaces();

  const workspace = workspaces?.find((candidate) => candidate.id === workspaceId);
  const section = sectionOf(pathname, workspaceId);

  return (
    <header className="border-line bg-canvas flex h-12 shrink-0 items-center gap-2 border-b px-3 sm:px-4 pointer-coarse:h-14">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onOpenNavigation}
        aria-label="Open navigation"
      >
        <Menu aria-hidden="true" />
      </Button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex items-center gap-1.5 text-base">
          {workspace ? (
            <li className="hidden min-w-0 items-center gap-1.5 sm:flex">
              <Link
                href={routes.documents(workspace.id)}
                className="text-fg-muted hover:text-fg max-w-48 truncate rounded-xs"
              >
                {workspace.name}
              </Link>
              {section ? (
                <span className="text-fg-subtle" aria-hidden="true">
                  /
                </span>
              ) : null}
            </li>
          ) : null}
          {section ? (
            <li className="text-fg truncate font-medium" aria-current="page">
              {section}
            </li>
          ) : null}
        </ol>
      </nav>

      <SearchTrigger onOpen={onOpenPalette} />
      <UserMenu />
    </header>
  );
}
