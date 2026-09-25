"use client";

import { useMemo } from "react";

import { FALLBACK_POLICY, type UploadPolicy } from "@/features/documents/upload/validate";
import { useServiceMeta } from "@/features/system/api/use-service-meta";

/**
 * What an upload may be, from the deployment itself (`/meta`), so the limit the browser
 * states is the limit the server enforces. Until it answers -- or if it cannot -- the
 * fallback checks only what does not depend on the deployment; the server checks the rest.
 */
export function useUploadPolicy(): UploadPolicy {
  const meta = useServiceMeta();
  const uploads = meta.data?.uploads;
  return useMemo(
    () =>
      uploads ? { maxBytes: uploads.max_bytes, extensions: uploads.extensions } : FALLBACK_POLICY,
    [uploads],
  );
}
