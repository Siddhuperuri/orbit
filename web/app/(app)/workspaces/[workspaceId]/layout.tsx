import { AnswerStreamsProvider } from "@/features/chat/streaming/answer-streams-provider";
import { UploadQueueProvider } from "@/features/documents/upload/upload-queue-provider";
import { WorkspaceBoundary } from "@/features/workspaces/components/workspace-boundary";

/**
 * Resolves the workspace named in the URL, then keeps its long-running work -- the
 * upload queue and in-progress answers -- alive across page changes inside it.
 * Everything below can assume a loaded workspace and a known role.
 *
 * `params` is a Promise in current Next.js; a layout awaits it.
 */
export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;

  return (
    <WorkspaceBoundary workspaceId={workspaceId}>
      <UploadQueueProvider workspaceId={workspaceId}>
        <AnswerStreamsProvider workspaceId={workspaceId}>{children}</AnswerStreamsProvider>
      </UploadQueueProvider>
    </WorkspaceBoundary>
  );
}
