import { PageContainer } from "@/components/layout/page-container";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";

/** A generic page-shaped placeholder: a title, a line, and a few rows. */
export function PageSkeleton() {
  return (
    <PageContainer>
      <LoadingRegion label="Loading page">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="mt-2 h-4 w-72 max-w-full" />
        <div className="mt-8 space-y-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </LoadingRegion>
    </PageContainer>
  );
}
