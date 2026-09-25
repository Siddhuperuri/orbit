"""The set of use cases the API layer is permitted to call.

Declared here, in `application`, rather than in the composition root, so that
`api/deps.py` can import the type to declare it -- `api` may depend on
`application` but not on `composition` (backend/.importlinter: `composition >
api > {application, infrastructure} > domain > core`). The composition root
still does all of the actual wiring; this module only names the shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from orbit.application.access import ResolveAccessContext
from orbit.application.answering.answer_question import AnswerQuestion
from orbit.application.auth.authenticate_access_token import AuthenticateAccessToken
from orbit.application.auth.email_verification import (
    RequestEmailVerification,
    VerifyEmail,
)
from orbit.application.auth.login_user import LoginUser
from orbit.application.auth.logout_session import LogoutSession
from orbit.application.auth.password_reset import (
    CompletePasswordReset,
    RequestPasswordReset,
)
from orbit.application.auth.refresh_session import RefreshSession
from orbit.application.auth.register_user import RegisterUser
from orbit.application.conversations.manage import (
    CreateConversation,
    DeleteConversation,
    GetConversation,
    ListConversations,
    ListMessages,
)
from orbit.application.documents.add_document_version import AddDocumentVersion
from orbit.application.documents.archive_document import ArchiveDocument
from orbit.application.documents.delete_document import DeleteDocument
from orbit.application.documents.get_document import GetDocument
from orbit.application.documents.get_document_download import GetDocumentDownload
from orbit.application.documents.list_document_versions import ListDocumentVersions
from orbit.application.documents.list_documents import ListDocuments
from orbit.application.documents.read_document_content import ReadDocumentContent
from orbit.application.documents.tag_document import AddDocumentTag, RemoveDocumentTag
from orbit.application.documents.update_document import UpdateDocument
from orbit.application.documents.upload_document import UploadDocument
from orbit.application.folders.manage import CreateFolder, DeleteFolder, ListFolders, RenameFolder
from orbit.application.processing.get_processing_status import GetProcessingStatus
from orbit.application.processing.reprocess_document import ReprocessDocument
from orbit.application.retrieval.hybrid_search import HybridSearch
from orbit.application.tags.manage import CreateTag, DeleteTag, ListTags, UpdateTag
from orbit.application.workspaces.create_workspace import CreateWorkspace
from orbit.application.workspaces.delete_workspace import DeleteWorkspace
from orbit.application.workspaces.get_workspace import GetWorkspace
from orbit.application.workspaces.list_workspaces import ListWorkspaces
from orbit.application.workspaces.members import (
    ChangeMemberRole,
    InviteMember,
    ListMembers,
    RemoveMember,
)
from orbit.application.workspaces.rename_workspace import RenameWorkspace


@dataclass(slots=True)
class UseCases:
    """Every application-layer use case, grouped so route handlers depend on
    one bag's shape rather than on the composition root."""

    register_user: RegisterUser
    login_user: LoginUser
    refresh_session: RefreshSession
    logout_session: LogoutSession
    authenticate_access_token: AuthenticateAccessToken
    request_password_reset: RequestPasswordReset
    complete_password_reset: CompletePasswordReset
    request_email_verification: RequestEmailVerification
    verify_email: VerifyEmail
    resolve_access_context: ResolveAccessContext
    create_workspace: CreateWorkspace
    list_workspaces: ListWorkspaces
    get_workspace: GetWorkspace
    rename_workspace: RenameWorkspace
    delete_workspace: DeleteWorkspace
    list_members: ListMembers
    invite_member: InviteMember
    change_member_role: ChangeMemberRole
    remove_member: RemoveMember
    list_documents: ListDocuments
    get_document: GetDocument
    update_document: UpdateDocument
    archive_document: ArchiveDocument
    delete_document: DeleteDocument
    list_document_versions: ListDocumentVersions
    read_document_content: ReadDocumentContent
    add_document_tag: AddDocumentTag
    remove_document_tag: RemoveDocumentTag
    list_folders: ListFolders
    create_folder: CreateFolder
    rename_folder: RenameFolder
    delete_folder: DeleteFolder
    list_tags: ListTags
    create_tag: CreateTag
    update_tag: UpdateTag
    delete_tag: DeleteTag
    upload_document: UploadDocument
    add_document_version: AddDocumentVersion
    get_document_download: GetDocumentDownload
    reprocess_document: ReprocessDocument
    get_processing_status: GetProcessingStatus
    search: HybridSearch
    create_conversation: CreateConversation
    list_conversations: ListConversations
    get_conversation: GetConversation
    list_messages: ListMessages
    delete_conversation: DeleteConversation
    answer_question: AnswerQuestion
