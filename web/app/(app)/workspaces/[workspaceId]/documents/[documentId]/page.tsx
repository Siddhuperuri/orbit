import { DocumentDetail } from "@/features/documents/components/document-detail";

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;
  return <DocumentDetail documentId={documentId} />;
}
