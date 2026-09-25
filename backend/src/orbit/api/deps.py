"""FastAPI dependency providers.

Use cases are constructed by the composition root and attached to application
state; this module hands them to route handlers. Nothing here constructs an
adapter, and nothing here imports ``orbit.infrastructure`` -- that boundary is
enforced by backend/.importlinter.

``app.state`` is untyped by design in Starlette, so each accessor casts to the
concrete application type. The cast is the single place the type is asserted;
if the composition root ever fails to set an attribute, startup fails rather
than a request failing later.
"""

from __future__ import annotations

import uuid
from typing import Annotated, cast

from fastapi import Depends, Path, Request

from orbit.api.cookies import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME
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
from orbit.application.health.check_readiness import CheckReadiness
from orbit.application.processing.get_processing_status import GetProcessingStatus
from orbit.application.processing.reprocess_document import ReprocessDocument
from orbit.application.registry import UseCases
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
from orbit.core.config import Settings
from orbit.core.logging import bind_correlation
from orbit.domain.access import AccessContext
from orbit.domain.errors import AuthenticationRequiredError
from orbit.domain.models.entities import User


async def bind_request_context(request: Request) -> None:
    """Attach the route's identifiers to every log record of the request.

    Installed as a dependency of every product router, so an incident query is
    ``workspace_id=... document_id=...`` rather than a regex over URLs. It is
    ``async`` on purpose: a synchronous dependency runs in a worker thread with
    a *copy* of the context, and what it binds would vanish before the handler
    ran.

    Path parameters are user input until proven otherwise, so only values that
    parse as UUIDs are bound -- a malformed one (which the route will reject
    with a 422) never reaches a log field.
    """
    params = request.path_params
    bind_correlation(
        workspace_id=_uuid_or_none(params.get("workspace_id")),
        document_id=_uuid_or_none(params.get("document_id")),
        operation=_operation_name(request),
    )


def _uuid_or_none(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value is not None else None
    except ValueError:
        return None


def _operation_name(request: Request) -> str | None:
    """``documents.upload_document``: the router module and handler name.

    Derived from code, never from the URL, so it is a small fixed vocabulary
    that can be grouped on.
    """
    endpoint = request.scope.get("endpoint")
    name = getattr(endpoint, "__name__", None)
    module = getattr(endpoint, "__module__", None)
    if not isinstance(name, str) or not isinstance(module, str):
        return None
    return f"{module.rsplit('.', 1)[-1]}.{name}"


def get_settings_dep(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_check_readiness(request: Request) -> CheckReadiness:
    return cast(CheckReadiness, request.app.state.check_readiness)


def _use_cases(request: Request) -> UseCases:
    return cast(UseCases, request.app.state.container.use_cases)


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
CheckReadinessDep = Annotated[CheckReadiness, Depends(get_check_readiness)]

# --------------------------------------------------------------------------
# Use cases. One named provider function per use case, each simply reading
# one attribute off `_use_cases`.
#
# These are named functions rather than inline lambdas specifically so a test
# can target them individually in `app.dependency_overrides`:
# `app.dependency_overrides[get_register_user] = lambda: stub_register_user`.
# A lambda has no importable identity to override by, which would force
# every API test to either build a real container or reach into `Annotated`
# metadata to find one -- this is the whole reason the indirection exists.
# --------------------------------------------------------------------------


def get_register_user(request: Request) -> RegisterUser:
    return _use_cases(request).register_user


def get_login_user(request: Request) -> LoginUser:
    return _use_cases(request).login_user


def get_refresh_session(request: Request) -> RefreshSession:
    return _use_cases(request).refresh_session


def get_logout_session(request: Request) -> LogoutSession:
    return _use_cases(request).logout_session


def get_request_password_reset(request: Request) -> RequestPasswordReset:
    return _use_cases(request).request_password_reset


def get_complete_password_reset(request: Request) -> CompletePasswordReset:
    return _use_cases(request).complete_password_reset


def get_request_email_verification(request: Request) -> RequestEmailVerification:
    return _use_cases(request).request_email_verification


def get_verify_email(request: Request) -> VerifyEmail:
    return _use_cases(request).verify_email


def get_authenticate_access_token(request: Request) -> AuthenticateAccessToken:
    return _use_cases(request).authenticate_access_token


def get_resolve_access_context(request: Request) -> ResolveAccessContext:
    return _use_cases(request).resolve_access_context


def get_create_workspace(request: Request) -> CreateWorkspace:
    return _use_cases(request).create_workspace


def get_list_workspaces(request: Request) -> ListWorkspaces:
    return _use_cases(request).list_workspaces


def get_get_workspace(request: Request) -> GetWorkspace:
    return _use_cases(request).get_workspace


def get_rename_workspace(request: Request) -> RenameWorkspace:
    return _use_cases(request).rename_workspace


def get_delete_workspace(request: Request) -> DeleteWorkspace:
    return _use_cases(request).delete_workspace


def get_list_members(request: Request) -> ListMembers:
    return _use_cases(request).list_members


def get_invite_member(request: Request) -> InviteMember:
    return _use_cases(request).invite_member


def get_change_member_role(request: Request) -> ChangeMemberRole:
    return _use_cases(request).change_member_role


def get_remove_member(request: Request) -> RemoveMember:
    return _use_cases(request).remove_member


def get_list_documents(request: Request) -> ListDocuments:
    return _use_cases(request).list_documents


def get_get_document(request: Request) -> GetDocument:
    return _use_cases(request).get_document


def get_update_document(request: Request) -> UpdateDocument:
    return _use_cases(request).update_document


def get_archive_document(request: Request) -> ArchiveDocument:
    return _use_cases(request).archive_document


def get_list_document_versions(request: Request) -> ListDocumentVersions:
    return _use_cases(request).list_document_versions


def get_read_document_content(request: Request) -> ReadDocumentContent:
    return _use_cases(request).read_document_content


def get_add_document_tag(request: Request) -> AddDocumentTag:
    return _use_cases(request).add_document_tag


def get_remove_document_tag(request: Request) -> RemoveDocumentTag:
    return _use_cases(request).remove_document_tag


def get_list_folders(request: Request) -> ListFolders:
    return _use_cases(request).list_folders


def get_create_folder(request: Request) -> CreateFolder:
    return _use_cases(request).create_folder


def get_rename_folder(request: Request) -> RenameFolder:
    return _use_cases(request).rename_folder


def get_delete_folder(request: Request) -> DeleteFolder:
    return _use_cases(request).delete_folder


def get_list_tags(request: Request) -> ListTags:
    return _use_cases(request).list_tags


def get_create_tag(request: Request) -> CreateTag:
    return _use_cases(request).create_tag


def get_update_tag(request: Request) -> UpdateTag:
    return _use_cases(request).update_tag


def get_delete_tag(request: Request) -> DeleteTag:
    return _use_cases(request).delete_tag


def get_delete_document(request: Request) -> DeleteDocument:
    return _use_cases(request).delete_document


def get_upload_document(request: Request) -> UploadDocument:
    return _use_cases(request).upload_document


def get_add_document_version(request: Request) -> AddDocumentVersion:
    return _use_cases(request).add_document_version


def get_get_document_download(request: Request) -> GetDocumentDownload:
    return _use_cases(request).get_document_download


def get_reprocess_document(request: Request) -> ReprocessDocument:
    return _use_cases(request).reprocess_document


def get_get_processing_status(request: Request) -> GetProcessingStatus:
    return _use_cases(request).get_processing_status


def get_search(request: Request) -> HybridSearch:
    return _use_cases(request).search


def get_create_conversation(request: Request) -> CreateConversation:
    return _use_cases(request).create_conversation


def get_list_conversations(request: Request) -> ListConversations:
    return _use_cases(request).list_conversations


def get_get_conversation(request: Request) -> GetConversation:
    return _use_cases(request).get_conversation


def get_list_messages(request: Request) -> ListMessages:
    return _use_cases(request).list_messages


def get_delete_conversation(request: Request) -> DeleteConversation:
    return _use_cases(request).delete_conversation


def get_answer_question(request: Request) -> AnswerQuestion:
    return _use_cases(request).answer_question


RegisterUserDep = Annotated[RegisterUser, Depends(get_register_user)]
LoginUserDep = Annotated[LoginUser, Depends(get_login_user)]
RefreshSessionDep = Annotated[RefreshSession, Depends(get_refresh_session)]
LogoutSessionDep = Annotated[LogoutSession, Depends(get_logout_session)]
RequestPasswordResetDep = Annotated[RequestPasswordReset, Depends(get_request_password_reset)]
CompletePasswordResetDep = Annotated[CompletePasswordReset, Depends(get_complete_password_reset)]
RequestEmailVerificationDep = Annotated[
    RequestEmailVerification, Depends(get_request_email_verification)
]
VerifyEmailDep = Annotated[VerifyEmail, Depends(get_verify_email)]
_AuthenticateAccessTokenDep = Annotated[
    AuthenticateAccessToken, Depends(get_authenticate_access_token)
]
_ResolveAccessContextDep = Annotated[ResolveAccessContext, Depends(get_resolve_access_context)]

CreateWorkspaceDep = Annotated[CreateWorkspace, Depends(get_create_workspace)]
ListWorkspacesDep = Annotated[ListWorkspaces, Depends(get_list_workspaces)]
GetWorkspaceDep = Annotated[GetWorkspace, Depends(get_get_workspace)]
RenameWorkspaceDep = Annotated[RenameWorkspace, Depends(get_rename_workspace)]
DeleteWorkspaceDep = Annotated[DeleteWorkspace, Depends(get_delete_workspace)]

ListMembersDep = Annotated[ListMembers, Depends(get_list_members)]
InviteMemberDep = Annotated[InviteMember, Depends(get_invite_member)]
ChangeMemberRoleDep = Annotated[ChangeMemberRole, Depends(get_change_member_role)]
RemoveMemberDep = Annotated[RemoveMember, Depends(get_remove_member)]

ListDocumentsDep = Annotated[ListDocuments, Depends(get_list_documents)]
GetDocumentDep = Annotated[GetDocument, Depends(get_get_document)]
UpdateDocumentDep = Annotated[UpdateDocument, Depends(get_update_document)]
ArchiveDocumentDep = Annotated[ArchiveDocument, Depends(get_archive_document)]
ListDocumentVersionsDep = Annotated[ListDocumentVersions, Depends(get_list_document_versions)]
ReadDocumentContentDep = Annotated[ReadDocumentContent, Depends(get_read_document_content)]
AddDocumentTagDep = Annotated[AddDocumentTag, Depends(get_add_document_tag)]
RemoveDocumentTagDep = Annotated[RemoveDocumentTag, Depends(get_remove_document_tag)]
ListFoldersDep = Annotated[ListFolders, Depends(get_list_folders)]
CreateFolderDep = Annotated[CreateFolder, Depends(get_create_folder)]
RenameFolderDep = Annotated[RenameFolder, Depends(get_rename_folder)]
DeleteFolderDep = Annotated[DeleteFolder, Depends(get_delete_folder)]
ListTagsDep = Annotated[ListTags, Depends(get_list_tags)]
CreateTagDep = Annotated[CreateTag, Depends(get_create_tag)]
UpdateTagDep = Annotated[UpdateTag, Depends(get_update_tag)]
DeleteTagDep = Annotated[DeleteTag, Depends(get_delete_tag)]
DeleteDocumentDep = Annotated[DeleteDocument, Depends(get_delete_document)]
UploadDocumentDep = Annotated[UploadDocument, Depends(get_upload_document)]
AddDocumentVersionDep = Annotated[AddDocumentVersion, Depends(get_add_document_version)]
GetDocumentDownloadDep = Annotated[GetDocumentDownload, Depends(get_get_document_download)]
ReprocessDocumentDep = Annotated[ReprocessDocument, Depends(get_reprocess_document)]
GetProcessingStatusDep = Annotated[GetProcessingStatus, Depends(get_get_processing_status)]
SearchDep = Annotated[HybridSearch, Depends(get_search)]
CreateConversationDep = Annotated[CreateConversation, Depends(get_create_conversation)]
ListConversationsDep = Annotated[ListConversations, Depends(get_list_conversations)]
GetConversationDep = Annotated[GetConversation, Depends(get_get_conversation)]
ListMessagesDep = Annotated[ListMessages, Depends(get_list_messages)]
DeleteConversationDep = Annotated[DeleteConversation, Depends(get_delete_conversation)]
AnswerQuestionDep = Annotated[AnswerQuestion, Depends(get_answer_question)]


# --------------------------------------------------------------------------
# Identity and authorization.
# --------------------------------------------------------------------------


def get_bearer_token(request: Request) -> str | None:
    """Extract the access token: cookie first, then `Authorization: Bearer`.

    Cookie first because it is the browser's transport (ADR-0009); the header
    exists for non-browser clients, which send no cookie at all, so checking
    it second never shadows a cookie a browser actually sent.
    """
    cookie_token = request.cookies.get(ACCESS_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value
    return None


def get_refresh_token(request: Request) -> str:
    """The refresh cookie, required.

    Unlike the access token there is no header fallback: the refresh token is
    long-lived and high-value, so it travels only by the transport that never
    reaches JavaScript.
    """
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not token:
        msg = "Sign in to continue."
        raise AuthenticationRequiredError(msg)
    return token


RefreshTokenDep = Annotated[str, Depends(get_refresh_token)]


async def get_current_user(request: Request, authenticate: _AuthenticateAccessTokenDep) -> User:
    token = get_bearer_token(request)
    if token is None:
        msg = "Sign in to continue."
        raise AuthenticationRequiredError(msg)
    user = await authenticate.execute(token)

    # Stashed on request state so the exception handler can name the actor in
    # a `PERMISSION_DENIED` audit record. The handler's signature is fixed by
    # Starlette, so it cannot take the user as a dependency; this is the only
    # channel between the two, and it is set only after authentication
    # succeeded -- an unauthenticated request leaves it absent rather than
    # guessed.
    request.state.user_id = user.id
    # From here on every log record of this request names the actor.
    bind_correlation(user_id=str(user.id))
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_access_context(
    workspace_id: Annotated[uuid.UUID, Path()],
    current_user: CurrentUserDep,
    resolve: _ResolveAccessContextDep,
) -> AccessContext:
    """Resolve the caller's `AccessContext` for the `{workspace_id}` in the path.

    Every workspace-scoped router depends on this rather than resolving
    membership itself, so there is exactly one place that decision is made
    (ADR-0004).
    """
    return await resolve.execute(user_id=current_user.id, workspace_id=workspace_id)


AccessContextDep = Annotated[AccessContext, Depends(get_access_context)]


def get_client_ip(request: Request) -> str | None:
    """The client address, used for rate limiting and the audit trail.

    `X-Forwarded-For` is consulted **only** when `ORBIT_TRUST_PROXY_HEADERS` is
    on. That gate is the security control, not a deployment convenience: any
    client can set the header, so trusting it unconditionally would let an
    attacker rotate it to obtain a fresh per-IP rate-limit key on every request
    -- defeating the limit precisely against the distributed attacker it exists
    to stop -- and would let them write arbitrary addresses into audit records.

    With the flag off, the socket peer address is used. Behind an unconfigured
    proxy that is the proxy's own address, which collapses every client into
    one bucket: wrong, but wrong in the safe direction, and visible the first
    time a limit trips for everyone at once.
    """
    settings = get_settings_dep(request)
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Left-most entry is the original client when the edge proxy
            # overwrites the header; entries appended by intermediaries follow.
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


ClientIpDep = Annotated[str | None, Depends(get_client_ip)]
UserAgentDep = Annotated[str | None, Depends(get_user_agent)]
