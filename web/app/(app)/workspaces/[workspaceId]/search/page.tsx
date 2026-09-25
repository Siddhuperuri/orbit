import type { Metadata } from "next";
import { Suspense } from "react";

import { PageSkeleton } from "@/components/layout/page-skeleton";
import { SearchPageContent } from "@/features/search/components/search-page-content";

export const metadata: Metadata = { title: "Search" };

export default function SearchPage() {
  // `useSearchParams` (the query lives in the URL) needs a Suspense boundary.
  return (
    <Suspense fallback={<PageSkeleton />}>
      <SearchPageContent />
    </Suspense>
  );
}
