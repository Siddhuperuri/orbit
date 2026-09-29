import { PageContainer } from "@/components/layout/page-container";
import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";

/** A generic page-shaped placeholder: a title card, a rule, and a few rows. */
export function PageSkeleton() {
  return (
    <PageContainer>
      <LoadingRegion label="Loading page">
        <Skeleton className="h-3 w-32" />
        <Skeleton className="mt-6 h-20 w-2/3 max-w-2xl" />
        <div className="border-line mt-10 space-y-px border-t pt-6">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      </LoadingRegion>
    </PageContainer>
  );
}
