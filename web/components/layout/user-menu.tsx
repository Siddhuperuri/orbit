"use client";

import { LogOut, Monitor, Moon, Settings, Sun } from "lucide-react";
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
import { initialsOf } from "@/lib/utils/format";

/** The account menu: identity, account settings, theme, and sign out. */
export function UserMenu() {
  const user = useUser();
  const logout = useLogout();
  const { preference, setPreference } = useTheme();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Account menu for ${user.full_name}`}
          className="bg-accent-soft text-accent-soft-fg inline-flex size-8 items-center justify-center rounded-full text-sm font-semibold hover:opacity-90 pointer-coarse:size-11"
        >
          <span aria-hidden="true">{initialsOf(user.full_name)}</span>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="space-y-0.5 py-2">
          <span className="text-fg block truncate text-base font-medium">{user.full_name}</span>
          <span className="text-fg-muted block truncate text-sm font-normal">{user.email}</span>
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
