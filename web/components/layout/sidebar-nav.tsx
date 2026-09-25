"use client";

import { FileText, MessageSquare, Search, Settings, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

interface NavItem {
  label: string;
  href: (workspaceId: string) => string;
  /** A path prefix, so a nested page (a document's detail) keeps its section highlighted. */
  match: (workspaceId: string) => string;
  icon: LucideIcon;
}

const ITEMS: NavItem[] = [
  {
    label: "Documents",
    href: routes.documents,
    match: (id) => `${routes.workspace(id)}/documents`,
    icon: FileText,
  },
  {
    label: "Search",
    href: (id) => routes.search(id),
    match: (id) => `${routes.workspace(id)}/search`,
    icon: Search,
  },
  {
    label: "Chat",
    href: (id) => routes.chat(id),
    match: (id) => `${routes.workspace(id)}/chat`,
    icon: MessageSquare,
  },
  {
    label: "Settings",
    href: routes.workspaceSettings,
    match: (id) => `${routes.workspace(id)}/settings`,
    icon: Settings,
  },
];

/**
 * Primary navigation for the current workspace. The active item carries
 * `aria-current="page"` -- the state a screen reader announces -- and a filled
 * background; the colour is never the only cue.
 */
export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const { workspaceId } = useParams<{ workspaceId?: string }>();
  const pathname = usePathname();

  if (!workspaceId) {
    return (
      <p className="text-fg-muted px-2 py-2 text-sm">
        Choose a workspace to see its documents, search, and chat.
      </p>
    );
  }

  return (
    <ul className="space-y-0.5">
      {ITEMS.map((item) => {
        const active = pathname.startsWith(item.match(workspaceId));
        return (
          <li key={item.label}>
            <Link
              href={item.href(workspaceId)}
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-base font-medium transition-colors pointer-coarse:h-11",
                active
                  ? "bg-accent-soft text-accent-soft-fg"
                  : "text-fg-muted hover:bg-line/60 hover:text-fg",
              )}
            >
              <item.icon className="size-4 shrink-0" aria-hidden="true" />
              {item.label}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
