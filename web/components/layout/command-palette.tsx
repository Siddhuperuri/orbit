"use client";

import {
  FileText,
  LogOut,
  MessageSquare,
  MessageSquarePlus,
  Monitor,
  Moon,
  Plus,
  Search,
  Settings,
  Sun,
  Users,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { useLogout } from "@/features/auth/api/use-auth-mutations";
import { usePaletteDocuments } from "@/features/documents/api/use-palette-documents";
import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
import { CreateWorkspaceDialog } from "@/features/workspaces/components/create-workspace-dialog";
import { setPrefilledQuestion } from "@/features/chat/lib/prefill";
import { routes } from "@/lib/navigation";
import { useTheme } from "@/lib/theme/use-theme";

/**
 * The command palette: jump anywhere, run common actions, or hand off a typed
 * query to search or chat. It is the app's keyboard-first entry point, but every
 * command in it also exists as ordinary UI -- the palette is a shortcut, never the
 * only way to do something.
 *
 * Typing offers two catch-all commands ("Search for …" and "Ask …"). They are
 * `forceMount`ed so the palette's own fuzzy filter cannot hide them: the whole
 * point of them is that they accept any text.
 */
export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const { workspaceId } = useParams<{ workspaceId?: string }>();
  const { data: workspaces } = useWorkspaces();
  const documents = usePaletteDocuments(workspaceId, open);
  const { setPreference } = useTheme();
  const logout = useLogout();

  const [query, setQuery] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);

  const text = query.trim();
  const inWorkspace = Boolean(workspaceId);

  function run(action: () => void) {
    onOpenChange(false);
    setQuery("");
    action();
  }

  const go = (href: string) => () => run(() => router.push(href));

  return (
    <>
      <CommandDialog
        open={open}
        onOpenChange={(next) => {
          onOpenChange(next);
          if (!next) setQuery("");
        }}
        title="Command palette"
        description="Search for a page, document, or action. Use the arrow keys to move and Enter to select."
      >
        <CommandInput
          value={query}
          onValueChange={setQuery}
          placeholder="Search documents, jump to a page, or run a command…"
        />
        <CommandList>
          <CommandEmpty>Nothing matches “{text}”.</CommandEmpty>

          {text && workspaceId ? (
            // The group must be force-mounted as well as its items: cmdk hides a group
            // that has no filter matches, even when every item in it is forced.
            <CommandGroup forceMount heading="Use your text">
              <CommandItem
                forceMount
                value={`search-for ${text}`}
                onSelect={go(routes.search(workspaceId, { q: text }))}
              >
                <Search aria-hidden="true" />
                <span className="truncate">Search documents for “{text}”</span>
              </CommandItem>
              <CommandItem
                forceMount
                value={`ask ${text}`}
                onSelect={() =>
                  run(() => {
                    setPrefilledQuestion(text);
                    router.push(routes.chat(workspaceId));
                  })
                }
              >
                <MessageSquare aria-hidden="true" />
                <span className="truncate">Ask about “{text}”</span>
              </CommandItem>
            </CommandGroup>
          ) : null}

          {workspaceId ? (
            <CommandGroup heading="Go to">
              <CommandItem onSelect={go(routes.documents(workspaceId))}>
                <FileText aria-hidden="true" />
                Documents
              </CommandItem>
              <CommandItem onSelect={go(routes.search(workspaceId))}>
                <Search aria-hidden="true" />
                Search
              </CommandItem>
              <CommandItem onSelect={go(routes.chat(workspaceId))}>
                <MessageSquare aria-hidden="true" />
                Chat
              </CommandItem>
              <CommandItem onSelect={go(routes.workspaceSettings(workspaceId))}>
                <Settings aria-hidden="true" />
                Workspace settings
              </CommandItem>
              <CommandItem onSelect={go(routes.members(workspaceId))}>
                <Users aria-hidden="true" />
                Members
              </CommandItem>
            </CommandGroup>
          ) : null}

          {workspaceId && documents.data && documents.data.length > 0 ? (
            <CommandGroup heading="Documents">
              {documents.data.map((document) => (
                <CommandItem
                  key={document.id}
                  value={`document ${document.title} ${document.id}`}
                  onSelect={go(routes.document(workspaceId, document.id))}
                >
                  <FileText aria-hidden="true" />
                  <span className="truncate font-serif">{document.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          ) : null}

          {workspaceId ? (
            <CommandGroup heading="Actions">
              <CommandItem onSelect={go(routes.chat(workspaceId))}>
                <MessageSquarePlus aria-hidden="true" />
                New conversation
              </CommandItem>
            </CommandGroup>
          ) : null}

          {workspaces && workspaces.length > 0 ? (
            <CommandGroup heading="Workspaces">
              {workspaces
                .filter((workspace) => workspace.id !== workspaceId)
                .map((workspace) => (
                  <CommandItem
                    key={workspace.id}
                    value={`workspace ${workspace.name}`}
                    onSelect={go(routes.documents(workspace.id))}
                  >
                    <FileText aria-hidden="true" />
                    Switch to {workspace.name}
                  </CommandItem>
                ))}
              <CommandItem
                value="create workspace"
                onSelect={() => run(() => setCreatingWorkspace(true))}
              >
                <Plus aria-hidden="true" />
                Create workspace
              </CommandItem>
            </CommandGroup>
          ) : null}

          <CommandGroup heading="Preferences">
            <CommandItem value="theme light" onSelect={() => run(() => setPreference("light"))}>
              <Sun aria-hidden="true" />
              Theme: Light
            </CommandItem>
            <CommandItem value="theme dark" onSelect={() => run(() => setPreference("dark"))}>
              <Moon aria-hidden="true" />
              Theme: Dark
            </CommandItem>
            <CommandItem value="theme system" onSelect={() => run(() => setPreference("system"))}>
              <Monitor aria-hidden="true" />
              Theme: Match system
            </CommandItem>
            <CommandItem onSelect={go(routes.account)}>
              <Settings aria-hidden="true" />
              Account settings
            </CommandItem>
            <CommandItem value="sign out" onSelect={() => run(() => logout.mutate())}>
              <LogOut aria-hidden="true" />
              Sign out
            </CommandItem>
          </CommandGroup>
        </CommandList>
      </CommandDialog>

      {inWorkspace || workspaces ? (
        <CreateWorkspaceDialog open={creatingWorkspace} onOpenChange={setCreatingWorkspace} />
      ) : null}
    </>
  );
}
