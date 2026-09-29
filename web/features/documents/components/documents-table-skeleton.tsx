import { LoadingRegion, Skeleton } from "@/components/ui/skeleton";

/** Rows shaped like the real table, so nothing shifts when the data arrives. */
export function DocumentsTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <LoadingRegion label="Loading documents" className="divide-line border-line divide-y border-y">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex items-center gap-4 px-4 py-4">
          <Skeleton className="hidden size-10 sm:block" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-5 w-2/5" />
            <Skeleton className="h-3 w-1/4" />
          </div>
          <Skeleton className="h-5 w-20" />
          <Skeleton className="hidden h-4 w-14 md:block" />
          <Skeleton className="hidden h-4 w-24 md:block" />
        </div>
      ))}
    </LoadingRegion>
  );
}
