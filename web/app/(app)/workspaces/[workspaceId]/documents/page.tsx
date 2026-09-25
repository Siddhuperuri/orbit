import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/layout/page-skeleton";
import { DocumentsPageContent } from "@/features/documents/components/documents-page-content";

export const metadata: Metadata = { title: "Documents" };

export default function DocumentsPage() {
  // `useSearchParams` (the status filter) needs a Suspense boundary.
  return (
    <Suspense fallback={<PageSkeleton />}>
      <DocumentsPageContent />
    </Suspense>
  );
}
