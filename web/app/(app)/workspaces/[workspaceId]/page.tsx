import { redirect } from "next/navigation";

import { routes } from "@/lib/navigation";

/** A workspace's landing page is its documents. */
export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(routes.documents(workspaceId));
}
