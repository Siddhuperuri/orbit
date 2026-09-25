import { z } from "zod";

export const MAX_DOCUMENT_TITLE_LENGTH = 512;

export const documentTitleSchema = z.object({
  title: z
    .string()
    .trim()
    .min(1, "A document needs a title.")
    .max(MAX_DOCUMENT_TITLE_LENGTH, `Use ${MAX_DOCUMENT_TITLE_LENGTH} characters or fewer.`),
});
export type DocumentTitleValues = z.infer<typeof documentTitleSchema>;
