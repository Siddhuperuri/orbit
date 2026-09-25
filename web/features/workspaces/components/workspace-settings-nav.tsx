"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useWorkspace } from "@/features/workspaces/hooks/use-workspace-context";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";

/** Sub-navigation between a workspace's settings pages. Plain links: each is a page, not a tab panel. */
export function WorkspaceSettingsNav() {
  const { workspace } = useWorkspace();
  const pathname = usePathname();

  const items = [
    { label: "General", href: routes.workspaceSettings(workspace.id) },
    { label: "Members", href: routes.members(workspace.id) },
  ];

  return (
    <nav aria-label="Workspace settings" className="border-line mb-6 border-b">
      <ul className="-mb-px flex gap-1">
        {items.map((item) => {
          const active = pathname === item.href;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-9 items-center border-b-2 px-3 text-base font-medium pointer-coarse:h-11",
                  active
                    ? "border-accent-solid text-fg"
                    : "text-fg-muted hover:text-fg border-transparent",
                )}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
