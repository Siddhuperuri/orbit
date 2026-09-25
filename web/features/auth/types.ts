import type { Schema } from "@/lib/api/types";

/** The signed-in account, exactly as the API describes it. */
export type User = Schema<"UserResponse">;
