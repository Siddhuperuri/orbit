"use client";

import Link from "next/link";

import { SidebarNav } from "@/components/layout/sidebar-nav";
import { UserMenu } from "@/components/layout/user-menu";
import { Wordmark } from "@/components/layout/wordmark";
import { WorkspaceSwitcher } from "@/features/workspaces/components/workspace-switcher";
import { routes } from "@/lib/navigation";

/**
 * The sidebar's content, shared by the persistent desktop sidebar and the
 * drawer used on narrower screens -- one definition, so the two can never drift.
 *
 * Top to bottom, each band ruled off from the next: who makes this (the mark),
 * where you are (the workspace), where you can go (navigation), and who you are
 * (the account, pinned to the foot).
 */
export function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-line flex h-14 shrink-0 items-center border-b px-5 pointer-coarse:h-16">
        <Link
          href={routes.home}
          onClick={onNavigate}
          aria-label="ORBIT home"
          className="group inline-flex rounded-md"
        >
          <Wordmark />
        </Link>
      </div>

      <div className="border-line border-b p-2">
        <WorkspaceSwitcher />
      </div>

      <nav aria-label="Primary" className="min-h-0 flex-1 overflow-y-auto py-3">
        <SidebarNav onNavigate={onNavigate} />
      </nav>

      <div className="border-line shrink-0 border-t p-2">
        <UserMenu variant="sidebar" />
      </div>
    </div>
  );
}
