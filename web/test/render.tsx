import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";
import { vi } from "vitest";

import { UserProvider } from "@/features/auth/hooks/use-user";
import type { User } from "@/features/auth/types";
import { AnswerStreamsProvider } from "@/features/chat/streaming/answer-streams-provider";
import { UploadQueueProvider } from "@/features/documents/upload/upload-queue-provider";
import { folderApi } from "@/features/folders/api/endpoints";
import { systemKeys } from "@/features/system/api/use-service-meta";
import { tagApi } from "@/features/tags/api/endpoints";
import { WorkspaceProvider } from "@/features/workspaces/hooks/use-workspace-context";
import type { Role, Workspace } from "@/features/workspaces/types";
import { navigation } from "@/test/navigation";

export const WORKSPACE_ID = "ws-1";
export const USER_ID = "user-1";

const NOW = "2026-09-19T12:00:00Z";

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: USER_ID,
    email: "ada@example.com",
    email_verified: true,
    full_name: "Ada Lovelace",
    created_at: NOW,
    ...overrides,
  };
}

export function makeWorkspace(role: Role, overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Acme",
    slug: "acme",
    version: 1,
    created_at: NOW,
    role,
    ...overrides,
  };
}

/** A client that never retries (a failure should show up as one) and never garbage-collects mid-test. */
export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
}

/**
 * Everything a workspace screen sits inside, for real: the query client, the signed-in user,
 * the workspace and the caller's role in it (so `can()` answers as it does in the app), and the
 * workspace-level upload queue and answer streams.
 *
 * The only things faked are the ones the page under test does not own. Folders, tags and the
 * deployment's upload policy are read by most of these screens incidentally, so they answer
 * "none" / "the usual" unless a test has already spied on them. Anything a test asserts on --
 * the document calls -- is spied on by that test, so an unexpected call is visible rather than
 * silently succeeding.
 */
export function renderInWorkspace(
  ui: ReactElement,
  {
    role = "member",
    user = makeUser(),
    client = makeQueryClient(),
    url = "/",
  }: { role?: Role; user?: User; client?: QueryClient; url?: string } = {},
) {
  navigation.reset(url);

  if (!vi.isMockFunction(folderApi.list)) {
    vi.spyOn(folderApi, "list").mockResolvedValue({ items: [] });
  }
  if (!vi.isMockFunction(tagApi.list)) {
    vi.spyOn(tagApi, "list").mockResolvedValue({ items: [] });
  }
  client.setQueryData(systemKeys.meta(), {
    service: "orbit-api",
    version: "0.0.0-test",
    api_version: "v1",
    environment: "test",
    uploads: { max_bytes: 10 * 1024 * 1024, extensions: [".md", ".pdf", ".txt"] },
  });

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <UserProvider value={user}>
          <WorkspaceProvider workspace={makeWorkspace(role)}>
            <UploadQueueProvider workspaceId={WORKSPACE_ID}>
              <AnswerStreamsProvider workspaceId={WORKSPACE_ID}>{children}</AnswerStreamsProvider>
            </UploadQueueProvider>
          </WorkspaceProvider>
        </UserProvider>
      </QueryClientProvider>
    );
  }

  return {
    client,
    user: userEvent.setup(),
    ...render(ui, { wrapper: Wrapper }),
  };
}
