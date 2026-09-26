"use client";

import { ChevronsUpDown, LogOut, Monitor, Moon, Settings, Sun } from "lucide-react";
import Link from "next/link";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useLogout } from "@/features/auth/api/use-auth-mutations";
import { useUser } from "@/features/auth/hooks/use-user";
import { useTheme } from "@/lib/theme/use-theme";
import type { ThemePreference } from "@/lib/theme/theme";
import { routes } from "@/lib/navigation";
import { cn } from "@/lib/utils/cn";
import { initialsOf } from "@/lib/utils/format";

function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "border-line-strong text-fg text-2xs inline-flex size-8 shrink-0 items-center justify-center rounded-full border font-mono font-medium tracking-wider",
        className,
      )}
    >
      {initialsOf(name)}
    </span>
  );
}

/**
 * The account menu: identity, account settings, theme, and sign out.
 *
 * `sidebar` is the full-width row pinned to the foot of the sidebar (name and
 * email beside the avatar); `compact` is the avatar alone, for the top bar on
 * screens where the sidebar is a drawer.
 */
export function UserMenu({ variant = "compact" }: { variant?: "sidebar" | "compact" }) {
  const user = useUser();
  const logout = useLogout();
  const { preference, setPreference } = useTheme();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {variant === "sidebar" ? (
          <button
            type="button"
            aria-label={`Account menu for ${user.full_name}`}
            className="hover:bg-fill data-[state=open]:bg-fill group flex h-12 w-full items-center gap-3 px-3 text-left transition-colors duration-300"
          >
            <Avatar name={user.full_name} />
            <span className="min-w-0 flex-1" aria-hidden="true">
              <span className="text-fg block truncate text-base leading-tight font-medium">
                {user.full_name}
              </span>
              <span className="text-fg-subtle text-2xs block truncate font-mono leading-tight">
                {user.email}
              </span>
            </span>
            <ChevronsUpDown
              className="text-fg-subtle group-hover:text-fg-muted size-4 shrink-0"
              aria-hidden="true"
            />
          </button>
        ) : (
          <button
            type="button"
            aria-label={`Account menu for ${user.full_name}`}
            className="border-line bg-surface/85 shadow-card inline-flex size-11 items-center justify-center rounded-2xl border backdrop-blur-md transition-opacity hover:opacity-90"
          >
            <Avatar name={user.full_name} />
          </button>
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align={variant === "sidebar" ? "start" : "end"}
        side={variant === "sidebar" ? "top" : "bottom"}
        className="w-64"
      >
        <DropdownMenuLabel className="flex items-center gap-2.5 py-2">
          <Avatar name={user.full_name} />
          <span className="min-w-0">
            <span className="text-fg block truncate text-base font-medium">{user.full_name}</span>
            <span className="text-fg-muted block truncate text-sm font-normal">{user.email}</span>
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        <DropdownMenuItem asChild>
          <Link href={routes.account}>
            <Settings aria-hidden="true" />
            Account settings
          </Link>
        </DropdownMenuItem>

        <DropdownMenuSeparator />
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={preference}
          onValueChange={(value) => setPreference(value as ThemePreference)}
        >
          <DropdownMenuRadioItem value="system">
            <Monitor aria-hidden="true" />
            System
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">
            <Sun aria-hidden="true" />
            Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon aria-hidden="true" />
            Dark
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>

        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={(event) => {
            // Keep the menu mounted until the request settles so a failure's
            // toast is not raised over a menu that vanished.
            event.preventDefault();
            logout.mutate();
          }}
        >
          <LogOut aria-hidden="true" />
          {logout.isPending ? "Signing out…" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
