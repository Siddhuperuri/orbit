"use client";

import Link from "next/link";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { Wordmark } from "@/components/layout/wordmark";
import { WorkspaceSwitcher } from "@/features/workspaces/components/workspace-switcher";
import { routes } from "@/lib/navigation";

/**
 * The sidebar's content, shared by the persistent desktop sidebar and the
 * drawer used on narrower screens -- one definition, so the two can never drift.
 */
export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-3.5 pt-4 pb-3">
        <Link
          href={routes.home}
          onClick={onNavigate}
          aria-label="ORBIT home"
          className="inline-flex rounded-md"
        >
          <Wordmark />
        </Link>
      </div>

      <div className="px-3 pb-3">
        <WorkspaceSwitcher />
      </div>

      <nav aria-label="Primary" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <SidebarNav onNavigate={onNavigate} />
      </nav>
    </div>
  );
}
