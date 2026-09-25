import type { Schema } from "@/lib/api/types";

/** A tag as returned by create/update. */
export type TagRecord = Schema<"TagResponse">;
/** A tag as listed: with how many documents carry it. */
export type Tag = Schema<"TagUsageResponse">;
/** The palette, as the API publishes it: an enum in the schema, so a new tone is a compile error here. */
export type TagColor = TagRecord["color"];
