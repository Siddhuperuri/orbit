"use client";

import {
  FileText,
  MessageSquareText,
  Search,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useActiveWorkspace } from "@/features/workspaces/hooks/use-active-workspace";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

export interface NavItem {
  label: string;
  href: (workspaceId: string) => string;
  /** Whether the current path belongs to this item, so a nested page keeps its section highlighted. */
  isActive: (pathname: string, workspaceId: string) => boolean;
  icon: LucideIcon;
}

const under = (section: string) => (pathname: string, id: string) =>
  pathname.startsWith(`${routes.workspace(id)}/${section}`);

const LIBRARY: NavItem[] = [
  { label: "Documents", href: routes.documents, isActive: under("documents"), icon: FileText },
  { label: "Search", href: (id) => routes.search(id), isActive: under("search"), icon: Search },
  {
    label: "Chat",
    href: (id) => routes.chat(id),
    isActive: under("chat"),
    icon: MessageSquareText,
  },
];

const WORKSPACE: NavItem[] = [
  {
    label: "Members",
    href: routes.members,
    isActive: under("settings/members"),
    icon: Users,
  },
  {
    label: "Settings",
    href: routes.workspaceSettings,
    isActive: (pathname, id) =>
      under("settings")(pathname, id) && !under("settings/members")(pathname, id),
    icon: Settings,
  },
];

/** Every section, in order, for the floating navbar (the drawer groups the same items). */
export const NAV_ITEMS: readonly NavItem[] = [...LIBRARY, ...WORKSPACE];

function NavGroup({
  label,
  items,
  workspaceId,
  pathname,
  onNavigate,
}: {
  label?: string;
  items: NavItem[];
  workspaceId: string;
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <div>
      {label ? (
        <p className="label-micro text-fg-subtle px-5 pt-6 pb-2" aria-hidden="true">
          {label}
        </p>
      ) : null}
      <ul>
        {items.map((item) => {
          const active = item.isActive(pathname, workspaceId);
          return (
            <li key={item.label}>
              <Link
                href={item.href(workspaceId)}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "label-caps group relative isolate flex h-10 items-center gap-3 overflow-hidden px-5 transition-colors duration-300 pointer-coarse:h-12",
                  // The current place is an inverted block; anywhere else, a fill wipes
                  // in from the left under the pointer.
                  active
                    ? "bg-fg text-canvas"
                    : cn(
                        "text-fg-muted hover:text-fg",
                        "before:bg-fill before:absolute before:inset-0 before:-z-10 before:origin-left before:scale-x-0 before:transition-transform before:duration-500 before:ease-out hover:before:scale-x-100",
                      ),
                )}
              >
                <item.icon
                  className={cn(
                    "size-4 shrink-0 transition-transform duration-500 ease-out",
                    active ? "text-canvas" : "text-fg-subtle group-hover:translate-x-0.5",
                  )}
                  strokeWidth={1.75}
                  aria-hidden="true"
                />
                <span className="transition-transform duration-500 ease-out group-hover:translate-x-0.5">
                  {item.label}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Primary navigation for the current workspace. The active item carries
 * `aria-current="page"` -- the state a screen reader announces -- and is set as an
 * inverted block, a change of shape as well as colour.
 *
 * Outside any workspace (the Account page) it keeps pointing at the workspace
 * the user was last in, rather than collapsing to nothing.
 */
export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { workspace } = useActiveWorkspace();
  const pathname = usePathname();

  if (!workspace) {
    return (
      <p className="text-fg-muted px-5 py-2 text-sm">
        Choose a workspace to see its documents, search, and chat.
      </p>
    );
  }

  return (
    <>
      <NavGroup
        items={LIBRARY}
        workspaceId={workspace.id}
        pathname={pathname}
        onNavigate={onNavigate}
      />
      <NavGroup
        label="Workspace"
        items={WORKSPACE}
        workspaceId={workspace.id}
        pathname={pathname}
        onNavigate={onNavigate}
      />
    </>
  );
}
