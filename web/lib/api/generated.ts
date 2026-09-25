/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Produced by `npm run api:types` from the backend's OpenAPI schema. Regenerate
 * it whenever an API route or schema changes; CI fails when it is stale.
 */

export interface paths {
    readonly "/api/v1/auth/login": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Authenticate with a password
         * @description Sets HttpOnly session cookies on success; the response body carries no token.
         */
        readonly post: operations["login_api_v1_auth_login_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/logout": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * End the session
         * @description Revokes the current token family and clears both cookies. Idempotent.
         */
        readonly post: operations["logout_api_v1_auth_logout_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/me": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** The authenticated account */
        readonly get: operations["me_api_v1_auth_me_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/password-reset": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Request a password reset link
         * @description Always returns 202, whether or not an account exists for the address. Any other behaviour would make this endpoint an unauthenticated account-enumeration oracle. Heavily rate limited: each accepted request costs an outbound email to an address the caller chose.
         */
        readonly post: operations["request_password_reset_api_v1_auth_password_reset_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/password-reset/confirm": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Set a new password with a reset token
         * @description Consumes the token, sets the password, and terminates every existing session for the account -- access tokens by bumping the token epoch, refresh tokens by explicit revocation. A reset that leaves the attacker's session alive has not recovered the account.
         */
        readonly post: operations["confirm_password_reset_api_v1_auth_password_reset_confirm_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/refresh": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Rotate the session
         * @description Redeems the refresh cookie for a new access/refresh pair. Reusing an already-redeemed refresh token revokes the entire session family, which is the mechanism that turns a stolen token into a detectable, self-limiting incident (ADR-0003).
         */
        readonly post: operations["refresh_api_v1_auth_refresh_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/register": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Create an account
         * @description Registration does not start a session; sign in afterward.
         */
        readonly post: operations["register_api_v1_auth_register_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/verify-email/confirm": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Confirm an email address
         * @description Deliberately unauthenticated: the link is followed from a mail client, which may not be the browser holding the session. The token is the only credential required, and it proves exactly one thing -- control of the address.
         */
        readonly post: operations["confirm_email_verification_api_v1_auth_verify_email_confirm_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/auth/verify-email/resend": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Resend the verification link
         * @description Requires a session. A no-op if the address is already verified.
         */
        readonly post: operations["resend_email_verification_api_v1_auth_verify_email_resend_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/meta": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Service metadata
         * @description Build and environment identity for the running API.
         */
        readonly get: operations["meta_api_v1_meta_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List my workspaces
         * @description Every workspace the caller is a member of, with their role in each.
         */
        readonly get: operations["list_workspaces_api_v1_workspaces_get"];
        readonly put?: never;
        /**
         * Create a workspace
         * @description The caller becomes its owner in the same transaction.
         */
        readonly post: operations["create_workspace_api_v1_workspaces_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get a workspace */
        readonly get: operations["get_workspace_api_v1_workspaces__workspace_id__get"];
        readonly put?: never;
        readonly post?: never;
        /**
         * Delete a workspace
         * @description Soft delete. Contents are retained and recoverable.
         */
        readonly delete: operations["delete_workspace_api_v1_workspaces__workspace_id__delete"];
        readonly options?: never;
        readonly head?: never;
        /**
         * Rename a workspace
         * @description Requires `expected_version` from the last read (optimistic concurrency).
         */
        readonly patch: operations["rename_workspace_api_v1_workspaces__workspace_id__patch"];
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/conversations": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List my conversations
         * @description The caller's own conversations in this workspace, newest first.
         */
        readonly get: operations["list_conversations_api_v1_workspaces__workspace_id__conversations_get"];
        readonly put?: never;
        /** Start a conversation */
        readonly post: operations["create_conversation_api_v1_workspaces__workspace_id__conversations_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get a conversation */
        readonly get: operations["get_conversation_api_v1_workspaces__workspace_id__conversations__conversation_id__get"];
        readonly put?: never;
        readonly post?: never;
        /** Delete a conversation */
        readonly delete: operations["delete_conversation_api_v1_workspaces__workspace_id__conversations__conversation_id__delete"];
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List a conversation's messages
         * @description In thread order, with citations. Pass `next_after` back as `after`.
         */
        readonly get: operations["list_messages_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_get"];
        readonly put?: never;
        /**
         * Ask a question
         * @description Retrieves evidence from the workspace, answers from it, and returns the assistant message with citations resolved against the retrieved chunks.
         */
        readonly post: operations["ask_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages/stream": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /** Ask a question, streaming the answer */
        readonly post: operations["ask_streaming_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_stream_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List documents
         * @description Keyset-paginated. Pass `next_cursor` back as `cursor`; a cursor is bound to the `sort` it was issued for and is refused under any other. Archived documents are excluded unless `archive=archived`. `q` is a case-insensitive substring of the *title* -- it is not content search (use search for that). `tag_id` may repeat; a document must carry every tag given.
         */
        readonly get: operations["list_documents_api_v1_workspaces__workspace_id__documents_get"];
        readonly put?: never;
        /**
         * Upload a document
         * @description The request body is the raw file content -- not `multipart/form-data`. `filename` is required; `title` defaults to the filename; `folder_id` is optional. Supported formats: PDF, Markdown, plain text. Re-uploading content that already exists in this workspace returns the existing document with `deduplicated: true`, rather than creating a second copy.
         */
        readonly post: operations["upload_document_api_v1_workspaces__workspace_id__documents_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** Get a document */
        readonly get: operations["get_document_api_v1_workspaces__workspace_id__documents__document_id__get"];
        readonly put?: never;
        readonly post?: never;
        /**
         * Delete a document
         * @description Soft delete, effective immediately. Reclamation of chunks and the stored object is asynchronous.
         */
        readonly delete: operations["delete_document_api_v1_workspaces__workspace_id__documents__document_id__delete"];
        readonly options?: never;
        readonly head?: never;
        /**
         * Rename and/or move a document
         * @description Send `title`, `folder_id`, or both. `folder_id: null` takes the document out of its folder; omitting it leaves the folder alone. Requires `expected_version` from the last read (optimistic concurrency): `409` if the document changed since.
         */
        readonly patch: operations["update_document_api_v1_workspaces__workspace_id__documents__document_id__patch"];
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/archive": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Archive a document
         * @description Takes the document out of the default list and out of search and answers. Nothing is removed, and it can be restored. Idempotent.
         */
        readonly post: operations["archive_document_api_v1_workspaces__workspace_id__documents__document_id__archive_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/content": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Read a document's indexed text
         * @description The current version's passages in reading order, with the overlap the chunker adds between neighbours removed. This is the text search and answers can see, not a rendering of the original file. Empty until the version is `ready`. Earlier versions keep their file but not their text.
         */
        readonly get: operations["get_document_content_api_v1_workspaces__workspace_id__documents__document_id__content_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/download": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Get a download link
         * @description A short-lived, single-object presigned URL for the current version's content. The API is not in the download data path: fetch `url` directly. It expires at `expires_at` and is not reusable after that.
         */
        readonly get: operations["get_document_download_api_v1_workspaces__workspace_id__documents__document_id__download_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/processing": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Get processing status
         * @description Where the current version is in the asynchronous pipeline -- `pending`, `processing`, `ready`, or `failed` with a reason -- and every attempt made. Poll this after an upload; processing never happens inside the upload request.
         */
        readonly get: operations["get_processing_status_api_v1_workspaces__workspace_id__documents__document_id__processing_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/reprocess": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Reprocess a failed document
         * @description Starts a fresh processing run for a document whose current version is `failed`, with a fresh retry budget. `409` if it is not failed.
         */
        readonly post: operations["reprocess_document_api_v1_workspaces__workspace_id__documents__document_id__reprocess_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/restore": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Restore an archived document
         * @description Idempotent: restoring a document that is not archived succeeds.
         */
        readonly post: operations["restore_document_api_v1_workspaces__workspace_id__documents__document_id__restore_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/tags/{tag_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        /**
         * Tag a document
         * @description Idempotent: adding a tag the document already has succeeds.
         */
        readonly put: operations["add_document_tag_api_v1_workspaces__workspace_id__documents__document_id__tags__tag_id__put"];
        readonly post?: never;
        /**
         * Remove a tag from a document
         * @description Idempotent: removing a tag the document does not have succeeds.
         */
        readonly delete: operations["remove_document_tag_api_v1_workspaces__workspace_id__documents__document_id__tags__tag_id__delete"];
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/versions": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List a document's versions
         * @description Every revision, newest first, current and superseded. Pass `next_before` back as `before` for older ones.
         */
        readonly get: operations["list_document_versions_api_v1_workspaces__workspace_id__documents__document_id__versions_get"];
        readonly put?: never;
        /**
         * Upload a new version of a document
         * @description Same validation as creating a document; replaces the current version.
         */
        readonly post: operations["add_document_version_api_v1_workspaces__workspace_id__documents__document_id__versions_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/documents/{document_id}/versions/{version_id}/download": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Get a download link for one version
         * @description As the document download, but for any revision. Superseded versions keep their stored file, so an earlier upload can still be retrieved.
         */
        readonly get: operations["get_document_version_download_api_v1_workspaces__workspace_id__documents__document_id__versions__version_id__download_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/folders": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List folders
         * @description Every folder in the workspace with what it holds, parents before children. Not paginated: the count per workspace is capped.
         */
        readonly get: operations["list_folders_api_v1_workspaces__workspace_id__folders_get"];
        readonly put?: never;
        /**
         * Create a folder
         * @description `409` if a sibling already has that name (compared case-insensitively).
         */
        readonly post: operations["create_folder_api_v1_workspaces__workspace_id__folders_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/folders/{folder_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        readonly post?: never;
        /**
         * Delete a folder
         * @description Only an empty folder can be deleted: `409 FOLDER_NOT_EMPTY` if it still holds documents (archived ones included) or subfolders. Nothing is moved or removed on the caller's behalf.
         */
        readonly delete: operations["delete_folder_api_v1_workspaces__workspace_id__folders__folder_id__delete"];
        readonly options?: never;
        readonly head?: never;
        /**
         * Rename a folder
         * @description Requires `expected_version` from the last read (optimistic concurrency).
         */
        readonly patch: operations["rename_folder_api_v1_workspaces__workspace_id__folders__folder_id__patch"];
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/members": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /** List members */
        readonly get: operations["list_members_api_v1_workspaces__workspace_id__members_get"];
        readonly put?: never;
        /** Add an existing account to the workspace */
        readonly post: operations["invite_member_api_v1_workspaces__workspace_id__members_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/members/{user_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        readonly post?: never;
        /**
         * Remove a member
         * @description Hard delete: access is revoked immediately, not merely hidden.
         */
        readonly delete: operations["remove_member_api_v1_workspaces__workspace_id__members__user_id__delete"];
        readonly options?: never;
        readonly head?: never;
        /** Change a member's role */
        readonly patch: operations["change_member_role_api_v1_workspaces__workspace_id__members__user_id__patch"];
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/search": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        /**
         * Search the workspace's documents
         * @description Hybrid retrieval: PostgreSQL full-text search and vector similarity, fused with Reciprocal Rank Fusion (k = 60). `mode` selects either retriever alone. Each result carries its document, version, chunk, source location, and each retriever's rank and score. If the embedding provider is unavailable, a hybrid request is answered lexically and says so in `retrievers` and `degraded`. Filters (`document_ids`, `folder_id`/`unfiled`, `tag_ids`, `content_types`) narrow the search inside the retrievers' SQL and can never widen it. Paging is by `offset`; `has_more` says whether another page exists. The `relevance` numbers are diagnostics for evaluation, not values to show a reader. Rate limited per account and per client address; exceeding either returns 429 with `Retry-After`.
         */
        readonly post: operations["search_api_v1_workspaces__workspace_id__search_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/tags": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * List tags
         * @description Every tag in the workspace by name, with how many documents carry it.
         */
        readonly get: operations["list_tags_api_v1_workspaces__workspace_id__tags_get"];
        readonly put?: never;
        /**
         * Create a tag
         * @description `409` if a tag with that name exists (compared case-insensitively).
         */
        readonly post: operations["create_tag_api_v1_workspaces__workspace_id__tags_post"];
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/api/v1/workspaces/{workspace_id}/tags/{tag_id}": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly get?: never;
        readonly put?: never;
        readonly post?: never;
        /**
         * Delete a tag
         * @description Removes the tag from every document that carries it. The documents are untouched.
         */
        readonly delete: operations["delete_tag_api_v1_workspaces__workspace_id__tags__tag_id__delete"];
        readonly options?: never;
        readonly head?: never;
        /**
         * Rename or recolour a tag
         * @description Requires `expected_version` from the last read (optimistic concurrency).
         */
        readonly patch: operations["update_tag_api_v1_workspaces__workspace_id__tags__tag_id__patch"];
        readonly trace?: never;
    };
    readonly "/healthz": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Liveness probe
         * @description Reports whether the process is running. Checks no external dependency, so a database or Redis outage does not cause healthy instances to be restarted.
         */
        readonly get: operations["liveness_healthz_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
    readonly "/readyz": {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        /**
         * Readiness probe
         * @description Reports whether this instance can serve traffic. Checks PostgreSQL, Redis, object storage, and the language-model circuit concurrently under a short timeout. Returns 503 only when a *critical* dependency (PostgreSQL, the vector schema) is down, so the instance leaves the load balancer's rotation without being restarted. A down non-critical dependency returns 200 with status `degraded`: the instance still serves everything that does not need it.
         */
        readonly get: operations["readiness_readyz_get"];
        readonly put?: never;
        readonly post?: never;
        readonly delete?: never;
        readonly options?: never;
        readonly head?: never;
        readonly patch?: never;
        readonly trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AnswerMetricsOut */
        readonly AnswerMetricsOut: {
            /** Context Tokens */
            readonly context_tokens: number | null;
            /** First Token Ms */
            readonly first_token_ms: number | null;
            /** Generation Ms */
            readonly generation_ms: number | null;
            /** Retrieval Degraded */
            readonly retrieval_degraded: string | null;
            /** Retrieval Ms */
            readonly retrieval_ms: number | null;
            /** Retrieved Count */
            readonly retrieved_count: number | null;
            /** Total Ms */
            readonly total_ms: number | null;
        };
        /**
         * ArchiveFilter
         * @description Which side of the archive a list shows. There is deliberately no "both":
         *     archived documents are out of the way, not mixed into the working set.
         * @enum {string}
         */
        readonly ArchiveFilter: "active" | "archived";
        /** AskRequest */
        readonly AskRequest: {
            /** Document Ids */
            readonly document_ids?: readonly string[] | null;
            /** Question */
            readonly question: string;
        };
        /** ChangeMemberRoleRequest */
        readonly ChangeMemberRoleRequest: {
            readonly role: components["schemas"]["Role"];
        };
        /** ChunkOut */
        readonly ChunkOut: {
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Ordinal */
            readonly ordinal: number;
            /** Text */
            readonly text: string;
        };
        /** CitationOut */
        readonly CitationOut: {
            readonly chunk: components["schemas"]["CitedChunkOut"];
            readonly document: components["schemas"]["CitedDocumentOut"];
            /** Handle */
            readonly handle: string;
            readonly location: components["schemas"]["CitedLocationOut"];
            /** Ordinal */
            readonly ordinal: number;
            /** Snippet */
            readonly snippet: string;
            readonly version: components["schemas"]["CitedVersionOut"];
        };
        /** CitedChunkOut */
        readonly CitedChunkOut: {
            /** Id */
            readonly id: string | null;
            /** Ordinal */
            readonly ordinal: number | null;
        };
        /** CitedDocumentOut */
        readonly CitedDocumentOut: {
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Title */
            readonly title: string;
        };
        /** CitedLocationOut */
        readonly CitedLocationOut: {
            /** Char End */
            readonly char_end: number | null;
            /** Char Start */
            readonly char_start: number | null;
            /** Heading Path */
            readonly heading_path: string | null;
            /** Page From */
            readonly page_from: number | null;
            /** Page To */
            readonly page_to: number | null;
        };
        /** CitedVersionOut */
        readonly CitedVersionOut: {
            /** Id */
            readonly id: string | null;
            /** Version Number */
            readonly version_number: number | null;
        };
        /** ConversationOut */
        readonly ConversationOut: {
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Title */
            readonly title: string;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
        };
        /** CreateConversationRequest */
        readonly CreateConversationRequest: {
            /** Title */
            readonly title?: string | null;
        };
        /** CreateFolderRequest */
        readonly CreateFolderRequest: {
            /** Name */
            readonly name: string;
            /**
             * Parent Id
             * @description Omit or `null` to create a top-level folder.
             */
            readonly parent_id?: string | null;
        };
        /** CreateTagRequest */
        readonly CreateTagRequest: {
            /**
             * Color
             * @default neutral
             * @enum {string}
             */
            readonly color: "neutral" | "accent" | "success" | "warning" | "danger";
            /** Name */
            readonly name: string;
        };
        /** CreateWorkspaceRequest */
        readonly CreateWorkspaceRequest: {
            /**
             * Name
             * @example Research
             */
            readonly name: string;
        };
        /** DependencyHealthResponse */
        readonly DependencyHealthResponse: {
            /**
             * Critical
             * @description Whether the instance can serve traffic without this dependency. A down non-critical dependency degrades a feature but keeps the instance in rotation.
             */
            readonly critical: boolean;
            /**
             * Detail
             * @description Generic failure summary. Never carries driver messages, hostnames, or credentials.
             */
            readonly detail?: string | null;
            /** Latency Ms */
            readonly latency_ms: number;
            /**
             * Name
             * @example database
             */
            readonly name: string;
            readonly status: components["schemas"]["DependencyStatus"];
        };
        /**
         * DependencyStatus
         * @enum {string}
         */
        readonly DependencyStatus: "up" | "down";
        /**
         * DocumentContentResponse
         * @description The indexed text of the current version, in reading order.
         *
         *     This is what search and answers can see -- not a rendering of the original
         *     file, whose layout and images are not retained.
         */
        readonly DocumentContentResponse: {
            /** Items */
            readonly items: readonly components["schemas"]["PassageResponse"][];
            /**
             * Next After
             * @description Pass as `after` for the next page. Absent on the last page.
             */
            readonly next_after?: number | null;
            /** Total Passages */
            readonly total_passages: number;
            /**
             * Version Id
             * Format: uuid
             */
            readonly version_id: string;
            /** Version Number */
            readonly version_number: number;
        };
        /** DocumentOut */
        readonly DocumentOut: {
            /** Content Type */
            readonly content_type: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Title */
            readonly title: string;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
        };
        /** DocumentResponse */
        readonly DocumentResponse: {
            /**
             * Archived At
             * @description Set while the document is archived: out of the default list and out of answers.
             */
            readonly archived_at: string | null;
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /** Created By User Id */
            readonly created_by_user_id: string | null;
            readonly current_version: components["schemas"]["DocumentVersionResponse"] | null;
            /** Folder Id */
            readonly folder_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Tags */
            readonly tags: readonly components["schemas"]["TagRefResponse"][];
            /** Title */
            readonly title: string;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Version
             * @description Optimistic-concurrency counter for this document's own row. Send it back as `expected_version` when editing. Not the revision number of the file -- that is `current_version.version_number`.
             */
            readonly version: number;
        };
        /**
         * DocumentSort
         * @description The orderings the list supports.
         *
         *     Each maps to one index (`ix_documents_*`), so any of them pages in constant
         *     time. The value is what travels in the URL and inside signed cursors.
         * @enum {string}
         */
        readonly DocumentSort: "created_desc" | "created_asc" | "updated_desc" | "title_asc" | "title_desc";
        /** DocumentVersionListResponse */
        readonly DocumentVersionListResponse: {
            /**
             * Items
             * @description Newest first.
             */
            readonly items: readonly components["schemas"]["DocumentVersionResponse"][];
            /**
             * Next Before
             * @description Pass as `before` for the next, older page. Absent on the last page.
             */
            readonly next_before?: number | null;
        };
        /** DocumentVersionResponse */
        readonly DocumentVersionResponse: {
            /** Byte Size */
            readonly byte_size: number;
            /** Chunk Count */
            readonly chunk_count: number;
            /**
             * Content Sha256
             * @description SHA-256 of the stored file, lowercase hex. Lets a person confirm which bytes a version holds.
             */
            readonly content_sha256: string;
            /** Content Type */
            readonly content_type: string;
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Created By User Id
             * @description Who uploaded this version; `null` if that account has since been deleted.
             */
            readonly created_by_user_id: string | null;
            /** Failure Code */
            readonly failure_code: string | null;
            /**
             * Failure Reason
             * @description Why processing failed, written for the person who uploaded the file. Present exactly when `status` is `failed`.
             */
            readonly failure_reason: string | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Is Current */
            readonly is_current: boolean;
            /** Original Filename */
            readonly original_filename: string;
            /** Page Count */
            readonly page_count: number | null;
            /** Processed At */
            readonly processed_at: string | null;
            /** @description Where the pipeline is right now, as last reported by the worker. Present only while `status` is `pending` or `processing`; there is no finer progress than this, so a client must not draw a percentage from it. */
            readonly processing_stage: components["schemas"]["PipelineStage"] | null;
            readonly status: components["schemas"]["ProcessingStatus"];
            /** Version Number */
            readonly version_number: number;
        };
        /** DownloadLinkResponse */
        readonly DownloadLinkResponse: {
            /**
             * Expires At
             * Format: date-time
             */
            readonly expires_at: string;
            /** Url */
            readonly url: string;
        };
        /** EmailVerificationConfirmRequest */
        readonly EmailVerificationConfirmRequest: {
            /** Token */
            readonly token: string;
        };
        /** EvidenceOut */
        readonly EvidenceOut: {
            /** Rank */
            readonly rank: number;
            /** Score */
            readonly score: number;
        };
        /**
         * FailureKind
         * @enum {string}
         */
        readonly FailureKind: "transient" | "permanent" | "defect";
        /** FailureOut */
        readonly FailureOut: {
            /** Code */
            readonly code: string;
            readonly stage: components["schemas"]["FailureStage"] | null;
        };
        /**
         * FailureStage
         * @description Which half of the pipeline failed. Never both, never ambiguous.
         * @enum {string}
         */
        readonly FailureStage: "retrieval" | "generation";
        /** FolderListResponse */
        readonly FolderListResponse: {
            /**
             * Items
             * @description Every folder, parents before children. Bounded per workspace, so unpaginated.
             */
            readonly items: readonly components["schemas"]["FolderNodeResponse"][];
        };
        /**
         * FolderNodeResponse
         * @description A folder with what is inside it, as listed.
         */
        readonly FolderNodeResponse: {
            /**
             * Archived Document Count
             * @description Archived documents directly inside. They keep the folder from being deleted.
             */
            readonly archived_document_count: number;
            /**
             * Child Count
             * @description Subfolders directly inside.
             */
            readonly child_count: number;
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /** Depth */
            readonly depth: number;
            /**
             * Document Count
             * @description Live, unarchived documents directly inside.
             */
            readonly document_count: number;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
            /** Parent Id */
            readonly parent_id: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Version
             * @description Send back as `expected_version` when renaming.
             */
            readonly version: number;
        };
        /** FolderResponse */
        readonly FolderResponse: {
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /** Depth */
            readonly depth: number;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
            /** Parent Id */
            readonly parent_id: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Version
             * @description Send back as `expected_version` when renaming.
             */
            readonly version: number;
        };
        /** FusionOut */
        readonly FusionOut: {
            /** Algorithm */
            readonly algorithm: string;
            /** K */
            readonly k: number;
        };
        /**
         * Grounding
         * @description How well an answer is supported by the workspace's documents.
         *
         *     Presented to the user, not hidden: an uncited answer is rendered as
         *     visibly weaker than a cited one (ADR-0006).
         * @enum {string}
         */
        readonly Grounding: "grounded" | "uncited" | "insufficient_evidence" | "no_evidence";
        /** HTTPValidationError */
        readonly HTTPValidationError: {
            /** Detail */
            readonly detail?: readonly components["schemas"]["ValidationError"][];
        };
        /** InviteMemberRequest */
        readonly InviteMemberRequest: {
            /** @default member */
            readonly role: components["schemas"]["Role"];
            /**
             * User Id
             * Format: uuid
             */
            readonly user_id: string;
        };
        /**
         * JobStatus
         * @description Mirrors the database enum.
         * @enum {string}
         */
        readonly JobStatus: "queued" | "running" | "succeeded" | "failed";
        /**
         * LivenessResponse
         * @description Answer to "is this process alive?"
         *
         *     Deliberately carries no dependency information: liveness must not depend on
         *     anything external (docs/decisions/0015-observability-strategy.md).
         */
        readonly LivenessResponse: {
            /** Service */
            readonly service: string;
            /**
             * Status
             * @default ok
             * @example ok
             */
            readonly status: string;
            /** Version */
            readonly version: string;
        };
        /** LocationOut */
        readonly LocationOut: {
            /** Char End */
            readonly char_end: number;
            /** Char Start */
            readonly char_start: number;
            /** Heading Path */
            readonly heading_path: string | null;
            /** Page From */
            readonly page_from: number | null;
            /** Page To */
            readonly page_to: number | null;
        };
        /** LoginRequest */
        readonly LoginRequest: {
            /** Email */
            readonly email: string;
            /** Password */
            readonly password: string;
        };
        /**
         * MatchedBy
         * @description Which retrievers returned a result.
         * @enum {string}
         */
        readonly MatchedBy: "lexical" | "semantic" | "both";
        /** MemberResponse */
        readonly MemberResponse: {
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            readonly role: components["schemas"]["Role"];
            /**
             * User Id
             * Format: uuid
             */
            readonly user_id: string;
        };
        /** MessageListOut */
        readonly MessageListOut: {
            /** Items */
            readonly items: readonly components["schemas"]["MessageOut"][];
            /** Next After */
            readonly next_after: number | null;
        };
        /** MessageOut */
        readonly MessageOut: {
            /** Citations */
            readonly citations?: readonly components["schemas"]["CitationOut"][];
            /** Content */
            readonly content: string;
            /**
             * Conversation Id
             * Format: uuid
             */
            readonly conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Discarded Citation Count
             * @default 0
             */
            readonly discarded_citation_count: number;
            readonly failure?: components["schemas"]["FailureOut"] | null;
            readonly grounding?: components["schemas"]["Grounding"] | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            readonly metrics?: components["schemas"]["AnswerMetricsOut"] | null;
            /** Model Id */
            readonly model_id?: string | null;
            /** Ordinal */
            readonly ordinal: number;
            /** Prompt Version */
            readonly prompt_version?: string | null;
            readonly role: components["schemas"]["MessageRole"];
            readonly status: components["schemas"]["MessageStatus"];
            readonly stop_reason?: components["schemas"]["StopReason"] | null;
            readonly usage?: components["schemas"]["UsageOut"] | null;
        };
        /**
         * MessageRole
         * @enum {string}
         */
        readonly MessageRole: "user" | "assistant";
        /**
         * MessageStatus
         * @enum {string}
         */
        readonly MessageStatus: "pending" | "complete" | "partial" | "failed";
        /** MetaResponse */
        readonly MetaResponse: {
            /**
             * Api Version
             * @default v1
             */
            readonly api_version: string;
            /**
             * Environment
             * @example development
             */
            readonly environment: string;
            /**
             * Service
             * @example orbit-api
             */
            readonly service: string;
            readonly uploads: components["schemas"]["UploadPolicy"];
            /**
             * Version
             * @example 0.1.0
             */
            readonly version: string;
        };
        /** PageResponse[ConversationOut] */
        readonly PageResponse_ConversationOut_: {
            /** Has More */
            readonly has_more: boolean;
            /** Items */
            readonly items: readonly components["schemas"]["ConversationOut"][];
            /**
             * Next Cursor
             * @description Pass as `cursor` to fetch the next page. Absent on the last page.
             */
            readonly next_cursor?: string | null;
        };
        /** PageResponse[DocumentResponse] */
        readonly PageResponse_DocumentResponse_: {
            /** Has More */
            readonly has_more: boolean;
            /** Items */
            readonly items: readonly components["schemas"]["DocumentResponse"][];
            /**
             * Next Cursor
             * @description Pass as `cursor` to fetch the next page. Absent on the last page.
             */
            readonly next_cursor?: string | null;
        };
        /** PassageResponse */
        readonly PassageResponse: {
            /** Char End */
            readonly char_end: number;
            /** Char Start */
            readonly char_start: number;
            /** Heading Path */
            readonly heading_path: string | null;
            /** Ordinal */
            readonly ordinal: number;
            /** Page From */
            readonly page_from: number | null;
            /** Page To */
            readonly page_to: number | null;
            /**
             * Text
             * @description The passage with text repeated from the previous one already removed.
             */
            readonly text: string;
        };
        /** PasswordResetConfirmRequest */
        readonly PasswordResetConfirmRequest: {
            /**
             * New Password
             * @example a-long-passphrase-is-fine
             */
            readonly new_password: string;
            /** Token */
            readonly token: string;
        };
        /**
         * PasswordResetRequest
         * @description Start a reset. The response is identical whether or not the account
         *     exists, so this schema has no success/failure variants to describe.
         */
        readonly PasswordResetRequest: {
            /**
             * Email
             * @example ada@example.com
             */
            readonly email: string;
        };
        /**
         * PipelineStage
         * @description The last stage a job reached. Recorded on the row as each begins, so a
         *     job abandoned by a dead worker still says where it died.
         * @enum {string}
         */
        readonly PipelineStage: "claimed" | "fetch" | "parse" | "normalize" | "chunk" | "embed" | "index" | "done";
        /**
         * ProcessingAttemptResponse
         * @description One processing attempt. Operator detail (`error_message`, the worker
         *     id) is deliberately absent: it can name internals, and the user-facing
         *     explanation lives on the version.
         */
        readonly ProcessingAttemptResponse: {
            /** Attempt */
            readonly attempt: number;
            /** Error Code */
            readonly error_code: string | null;
            readonly failure_kind: components["schemas"]["FailureKind"] | null;
            /** Finished At */
            readonly finished_at: string | null;
            /**
             * Scheduled For
             * Format: date-time
             */
            readonly scheduled_for: string;
            readonly stage: components["schemas"]["PipelineStage"] | null;
            /** Started At */
            readonly started_at: string | null;
            readonly status: components["schemas"]["JobStatus"];
        };
        /**
         * ProcessingStatus
         * @description Mirrors the database enum. Declared here so the domain does not import
         *     infrastructure to name its own states.
         * @enum {string}
         */
        readonly ProcessingStatus: "pending" | "processing" | "ready" | "failed";
        /** ProcessingStatusResponse */
        readonly ProcessingStatusResponse: {
            /**
             * Attempts
             * @description Newest attempt first.
             */
            readonly attempts: readonly components["schemas"]["ProcessingAttemptResponse"][];
            /**
             * Document Id
             * Format: uuid
             */
            readonly document_id: string;
            readonly version: components["schemas"]["DocumentVersionResponse"];
        };
        /**
         * ReadinessResponse
         * @description Answer to "can this process serve traffic?"
         *
         *     Returned with 200 when ready and 503 when not, so an orchestrator can act on
         *     the status code alone while a human gets the per-dependency breakdown.
         */
        readonly ReadinessResponse: {
            /** Dependencies */
            readonly dependencies: readonly components["schemas"]["DependencyHealthResponse"][];
            /**
             * Status
             * @description `ready`: everything is up. `degraded`: serving, but a non-critical dependency is down (200). `not_ready`: a critical dependency is down; take the instance out of rotation (503).
             * @example ready
             * @example degraded
             * @example not_ready
             */
            readonly status: string;
        };
        /** RegisterRequest */
        readonly RegisterRequest: {
            /**
             * Email
             * @example ada@example.com
             */
            readonly email: string;
            /**
             * Full Name
             * @example Ada Lovelace
             */
            readonly full_name: string;
            /**
             * Password
             * @example a-long-passphrase-is-fine
             */
            readonly password: string;
        };
        /**
         * RelevanceOut
         * @description Why a result placed where it did.
         *
         *     These are *diagnostics*, for evaluation, benchmarks, and debugging a
         *     ranking -- not numbers to put in front of a reader. The fused score has no
         *     meaning outside the response that produced it, and the retrievers' native
         *     scores are on different scales; a user shown "0.0164" learns nothing they
         *     can act on. Clients should present `matched_by` and position instead.
         */
        readonly RelevanceOut: {
            readonly lexical: components["schemas"]["EvidenceOut"] | null;
            /** Score */
            readonly score: number;
            readonly semantic: components["schemas"]["EvidenceOut"] | null;
        };
        /** RenameFolderRequest */
        readonly RenameFolderRequest: {
            /** Expected Version */
            readonly expected_version: number;
            /** Name */
            readonly name: string;
        };
        /** RenameWorkspaceRequest */
        readonly RenameWorkspaceRequest: {
            /** Expected Version */
            readonly expected_version: number;
            /** Name */
            readonly name: string;
        };
        /**
         * RetrievalMethod
         * @description One retriever.
         * @enum {string}
         */
        readonly RetrievalMethod: "lexical" | "semantic";
        /**
         * RetrievalMode
         * @enum {string}
         */
        readonly RetrievalMode: "hybrid" | "lexical" | "semantic";
        /**
         * Role
         * @description A member's role within one workspace.
         * @enum {string}
         */
        readonly Role: "owner" | "admin" | "member" | "viewer";
        /**
         * SearchRequest
         * @description A search, and what to narrow it to.
         *
         *     Every filter narrows and none widens: they are conjoined with the
         *     caller's workspace inside the retrievers' SQL, so an id belonging to
         *     another tenant matches nothing rather than reaching across the boundary.
         *     Folder and tag semantics match the document list exactly -- a folder is
         *     the documents directly in it, and tags are conjunctive.
         */
        readonly SearchRequest: {
            /** Content Types */
            readonly content_types?: readonly string[] | null;
            /** Document Ids */
            readonly document_ids?: readonly string[] | null;
            /** Folder Id */
            readonly folder_id?: string | null;
            /**
             * Limit
             * @default 10
             */
            readonly limit: number;
            /** @default hybrid */
            readonly mode: components["schemas"]["RetrievalMode"];
            /**
             * Offset
             * @default 0
             */
            readonly offset: number;
            /** Query */
            readonly query: string;
            /** Tag Ids */
            readonly tag_ids?: readonly string[] | null;
            /**
             * Unfiled
             * @default false
             */
            readonly unfiled: boolean;
        };
        /** SearchResponseOut */
        readonly SearchResponseOut: {
            /** Candidates */
            readonly candidates: {
                readonly [key: string]: number;
            };
            /** Degraded */
            readonly degraded: string | null;
            /** Filtered */
            readonly filtered: boolean;
            readonly fusion: components["schemas"]["FusionOut"];
            /** Has More */
            readonly has_more: boolean;
            readonly mode: components["schemas"]["RetrievalMode"];
            /** Offset */
            readonly offset: number;
            /** Query */
            readonly query: string;
            /** Results */
            readonly results: readonly components["schemas"]["SearchResultOut"][];
            /** Retrievers */
            readonly retrievers: readonly components["schemas"]["RetrievalMethod"][];
        };
        /** SearchResultOut */
        readonly SearchResultOut: {
            readonly chunk: components["schemas"]["ChunkOut"];
            readonly document: components["schemas"]["DocumentOut"];
            readonly location: components["schemas"]["LocationOut"];
            readonly matched_by: components["schemas"]["MatchedBy"];
            /** Rank */
            readonly rank: number;
            readonly relevance: components["schemas"]["RelevanceOut"];
            readonly version: components["schemas"]["VersionOut"];
        };
        /**
         * SessionResponse
         * @description Confirms who is now authenticated. The session itself lives in cookies.
         */
        readonly SessionResponse: {
            readonly user: components["schemas"]["UserResponse"];
        };
        /**
         * StopReason
         * @enum {string}
         */
        readonly StopReason: "completed" | "max_tokens" | "content_filtered" | "cancelled" | "timeout" | "provider_error" | "malformed_response" | "retrieval_failed" | "abandoned";
        /** TagListResponse */
        readonly TagListResponse: {
            /**
             * Items
             * @description Every tag, by name. Bounded per workspace, so unpaginated.
             */
            readonly items: readonly components["schemas"]["TagUsageResponse"][];
        };
        /**
         * TagRefResponse
         * @description A tag as it appears on a document: enough to draw its chip.
         */
        readonly TagRefResponse: {
            /**
             * Color
             * @enum {string}
             */
            readonly color: "neutral" | "accent" | "success" | "warning" | "danger";
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
        };
        /** TagResponse */
        readonly TagResponse: {
            /**
             * Color
             * @enum {string}
             */
            readonly color: "neutral" | "accent" | "success" | "warning" | "danger";
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Version
             * @description Send back as `expected_version` when editing.
             */
            readonly version: number;
        };
        /** TagUsageResponse */
        readonly TagUsageResponse: {
            /**
             * Color
             * @enum {string}
             */
            readonly color: "neutral" | "accent" | "success" | "warning" | "danger";
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Document Count
             * @description Live documents carrying the tag, archived ones included.
             */
            readonly document_count: number;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
            /**
             * Updated At
             * Format: date-time
             */
            readonly updated_at: string;
            /**
             * Version
             * @description Send back as `expected_version` when editing.
             */
            readonly version: number;
        };
        /**
         * UpdateDocumentRequest
         * @description A partial edit of a document's identity: its title, its folder, or both.
         *
         *     `folder_id` distinguishes three intents by *presence*, which JSON Merge Patch
         *     semantics require and a plain optional field cannot express: absent leaves
         *     the folder alone, `null` takes the document out of its folder, and an id
         *     files it there. `title` is simply optional.
         */
        readonly UpdateDocumentRequest: {
            /** Expected Version */
            readonly expected_version: number;
            /** Folder Id */
            readonly folder_id?: string | null;
            /** Title */
            readonly title?: string | null;
        };
        /** UpdateTagRequest */
        readonly UpdateTagRequest: {
            /** Color */
            readonly color?: ("neutral" | "accent" | "success" | "warning" | "danger") | null;
            /** Expected Version */
            readonly expected_version: number;
            /** Name */
            readonly name?: string | null;
        };
        /**
         * UploadPolicy
         * @description What an upload may be, so a client can refuse a file before sending it.
         *
         *     Advisory only: the server re-checks every one of these, and sniffs the real
         *     type from the bytes. Exposing them means the browser states the *actual*
         *     limit instead of a hardcoded copy that drifts from the deployment.
         */
        readonly UploadPolicy: {
            /**
             * Extensions
             * @example [
             *       ".md",
             *       ".pdf",
             *       ".txt"
             *     ]
             */
            readonly extensions: readonly string[];
            /**
             * Max Bytes
             * @example 52428800
             */
            readonly max_bytes: number;
        };
        /**
         * UploadResponse
         * @description The outcome of an upload: which document it landed on, and whether that
         *     document already existed (ADR-0011's deduplication).
         */
        readonly UploadResponse: {
            /**
             * Deduplicated
             * @description True if this content already existed as another document's current version. The upload succeeded, but no new document or version was created -- `document` is the pre-existing one.
             */
            readonly deduplicated: boolean;
            readonly document: components["schemas"]["DocumentResponse"];
        };
        /** UsageOut */
        readonly UsageOut: {
            /** Completion Tokens */
            readonly completion_tokens: number | null;
            /** Prompt Tokens */
            readonly prompt_tokens: number | null;
        };
        /** UserResponse */
        readonly UserResponse: {
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /** Email */
            readonly email: string;
            /** Email Verified */
            readonly email_verified: boolean;
            /** Full Name */
            readonly full_name: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
        };
        /** ValidationError */
        readonly ValidationError: {
            /** Location */
            readonly loc: readonly (string | number)[];
            /** Message */
            readonly msg: string;
            /** Error Type */
            readonly type: string;
        };
        /** VersionOut */
        readonly VersionOut: {
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Version Number */
            readonly version_number: number;
        };
        /** WorkspaceResponse */
        readonly WorkspaceResponse: {
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Name */
            readonly name: string;
            /** @description The caller's role, when known from a membership listing. */
            readonly role?: components["schemas"]["Role"] | null;
            /** Slug */
            readonly slug: string;
            /** Version */
            readonly version: number;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    readonly login_api_v1_auth_login_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["LoginRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SessionResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly logout_api_v1_auth_logout_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    readonly me_api_v1_auth_me_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["UserResponse"];
                };
            };
        };
    };
    readonly request_password_reset_api_v1_auth_password_reset_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["PasswordResetRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 202: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly confirm_password_reset_api_v1_auth_password_reset_confirm_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["PasswordResetConfirmRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly refresh_api_v1_auth_refresh_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SessionResponse"];
                };
            };
        };
    };
    readonly register_api_v1_auth_register_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["RegisterRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["UserResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly confirm_email_verification_api_v1_auth_verify_email_confirm_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["EmailVerificationConfirmRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly resend_email_verification_api_v1_auth_verify_email_resend_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 202: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    readonly meta_api_v1_meta_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MetaResponse"];
                };
            };
        };
    };
    readonly list_workspaces_api_v1_workspaces_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": readonly components["schemas"]["WorkspaceResponse"][];
                };
            };
        };
    };
    readonly create_workspace_api_v1_workspaces_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["CreateWorkspaceRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["WorkspaceResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_workspace_api_v1_workspaces__workspace_id__get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["WorkspaceResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly delete_workspace_api_v1_workspaces__workspace_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly rename_workspace_api_v1_workspaces__workspace_id__patch: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["RenameWorkspaceRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["WorkspaceResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_conversations_api_v1_workspaces__workspace_id__conversations_get: {
        readonly parameters: {
            readonly query?: {
                readonly cursor?: string | null;
                readonly limit?: number;
            };
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["PageResponse_ConversationOut_"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly create_conversation_api_v1_workspaces__workspace_id__conversations_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["CreateConversationRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ConversationOut"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_conversation_api_v1_workspaces__workspace_id__conversations__conversation_id__get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly conversation_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ConversationOut"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly delete_conversation_api_v1_workspaces__workspace_id__conversations__conversation_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly conversation_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_messages_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_get: {
        readonly parameters: {
            readonly query?: {
                readonly after?: number | null;
                readonly limit?: number;
            };
            readonly header?: never;
            readonly path: {
                readonly conversation_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MessageListOut"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly ask_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly conversation_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["AskRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MessageOut"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly ask_streaming_api_v1_workspaces__workspace_id__conversations__conversation_id__messages_stream_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly conversation_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["AskRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "text/event-stream": unknown;
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_documents_api_v1_workspaces__workspace_id__documents_get: {
        readonly parameters: {
            readonly query?: {
                readonly archive?: components["schemas"]["ArchiveFilter"];
                readonly cursor?: string | null;
                readonly folder_id?: string | null;
                readonly limit?: number;
                readonly q?: string | null;
                readonly sort?: components["schemas"]["DocumentSort"];
                readonly status?: components["schemas"]["ProcessingStatus"] | null;
                readonly tag_id?: readonly string[] | null;
                /** @description Only documents in no folder. Not combinable with `folder_id`. */
                readonly unfiled?: boolean;
            };
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["PageResponse_DocumentResponse_"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly upload_document_api_v1_workspaces__workspace_id__documents_post: {
        readonly parameters: {
            readonly query: {
                readonly filename: string;
                readonly folder_id?: string | null;
                readonly title?: string | null;
            };
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["UploadResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_document_api_v1_workspaces__workspace_id__documents__document_id__get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly delete_document_api_v1_workspaces__workspace_id__documents__document_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly update_document_api_v1_workspaces__workspace_id__documents__document_id__patch: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["UpdateDocumentRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly archive_document_api_v1_workspaces__workspace_id__documents__document_id__archive_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_document_content_api_v1_workspaces__workspace_id__documents__document_id__content_get: {
        readonly parameters: {
            readonly query?: {
                readonly after?: number | null;
                readonly limit?: number;
            };
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentContentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_document_download_api_v1_workspaces__workspace_id__documents__document_id__download_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DownloadLinkResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_processing_status_api_v1_workspaces__workspace_id__documents__document_id__processing_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ProcessingStatusResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly reprocess_document_api_v1_workspaces__workspace_id__documents__document_id__reprocess_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 202: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly restore_document_api_v1_workspaces__workspace_id__documents__document_id__restore_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly add_document_tag_api_v1_workspaces__workspace_id__documents__document_id__tags__tag_id__put: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly tag_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly remove_document_tag_api_v1_workspaces__workspace_id__documents__document_id__tags__tag_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly tag_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_document_versions_api_v1_workspaces__workspace_id__documents__document_id__versions_get: {
        readonly parameters: {
            readonly query?: {
                readonly before?: number | null;
                readonly limit?: number;
            };
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DocumentVersionListResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly add_document_version_api_v1_workspaces__workspace_id__documents__document_id__versions_post: {
        readonly parameters: {
            readonly query: {
                readonly filename: string;
            };
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["UploadResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly get_document_version_download_api_v1_workspaces__workspace_id__documents__document_id__versions__version_id__download_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly document_id: string;
                readonly version_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["DownloadLinkResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_folders_api_v1_workspaces__workspace_id__folders_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["FolderListResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly create_folder_api_v1_workspaces__workspace_id__folders_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["CreateFolderRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["FolderResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly delete_folder_api_v1_workspaces__workspace_id__folders__folder_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly folder_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly rename_folder_api_v1_workspaces__workspace_id__folders__folder_id__patch: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly folder_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["RenameFolderRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["FolderResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_members_api_v1_workspaces__workspace_id__members_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": readonly components["schemas"]["MemberResponse"][];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly invite_member_api_v1_workspaces__workspace_id__members_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["InviteMemberRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MemberResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly remove_member_api_v1_workspaces__workspace_id__members__user_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly user_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly change_member_role_api_v1_workspaces__workspace_id__members__user_id__patch: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly user_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["ChangeMemberRoleRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["MemberResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly search_api_v1_workspaces__workspace_id__search_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["SearchRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["SearchResponseOut"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly list_tags_api_v1_workspaces__workspace_id__tags_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["TagListResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly create_tag_api_v1_workspaces__workspace_id__tags_post: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["CreateTagRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 201: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["TagResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly delete_tag_api_v1_workspaces__workspace_id__tags__tag_id__delete: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly tag_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 204: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly update_tag_api_v1_workspaces__workspace_id__tags__tag_id__patch: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path: {
                readonly tag_id: string;
                readonly workspace_id: string;
            };
            readonly cookie?: never;
        };
        readonly requestBody: {
            readonly content: {
                readonly "application/json": components["schemas"]["UpdateTagRequest"];
            };
        };
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["TagResponse"];
                };
            };
            /** @description Validation Error */
            readonly 422: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readonly liveness_healthz_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["LivenessResponse"];
                };
            };
        };
    };
    readonly readiness_readyz_get: {
        readonly parameters: {
            readonly query?: never;
            readonly header?: never;
            readonly path?: never;
            readonly cookie?: never;
        };
        readonly requestBody?: never;
        readonly responses: {
            /** @description Successful Response */
            readonly 200: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
            /** @description At least one dependency is unavailable. */
            readonly 503: {
                headers: {
                    readonly [name: string]: unknown;
                };
                content: {
                    readonly "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
        };
    };
}
