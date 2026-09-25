"""Authentication endpoints.

Route handlers translate HTTP to a use-case call and back -- they validate
nothing beyond what Pydantic already validated, decide nothing, and touch no
repository directly (backend/.importlinter forbids `api` from importing
`orbit.infrastructure` at all, which makes that structural rather than a
convention).
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import Response as EmptyResponse

from orbit.api.cookies import clear_session_cookies, set_session_cookies
from orbit.api.deps import (
    ClientIpDep,
    CompletePasswordResetDep,
    CurrentUserDep,
    LoginUserDep,
    LogoutSessionDep,
    RefreshSessionDep,
    RefreshTokenDep,
    RegisterUserDep,
    RequestEmailVerificationDep,
    RequestPasswordResetDep,
    SettingsDep,
    UserAgentDep,
    VerifyEmailDep,
)
from orbit.api.v1.schemas.auth import (
    EmailVerificationConfirmRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
    SessionResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    description="Registration does not start a session; sign in afterward.",
)
async def register(
    body: RegisterRequest,
    register_user: RegisterUserDep,
    request_verification: RequestEmailVerificationDep,
    client_ip: ClientIpDep,
    user_agent: UserAgentDep,
) -> UserResponse:
    user = await register_user.execute(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    # Best-effort, and deliberately not awaited for its outcome beyond
    # completion: a mail provider that is down must not fail a
    # registration that has already committed. The user can resend.
    await request_verification.execute(user_id=user.id, client_ip=client_ip)
    return UserResponse.from_entity(user)


@router.post(
    "/login",
    response_model=SessionResponse,
    summary="Authenticate with a password",
    description="Sets HttpOnly session cookies on success; the response body carries no token.",
)
async def login(
    body: LoginRequest,
    response: Response,
    login_user: LoginUserDep,
    settings: SettingsDep,
    client_ip: ClientIpDep,
    user_agent: UserAgentDep,
) -> SessionResponse:
    session = await login_user.execute(
        email=body.email, password=body.password, client_ip=client_ip, user_agent=user_agent
    )
    set_session_cookies(response, session, settings)
    return SessionResponse(user=UserResponse.from_entity(session.user))


@router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="Rotate the session",
    description=(
        "Redeems the refresh cookie for a new access/refresh pair. Reusing an "
        "already-redeemed refresh token revokes the entire session family, "
        "which is the mechanism that turns a stolen token into a detectable, "
        "self-limiting incident (ADR-0003)."
    ),
)
async def refresh(
    response: Response,
    refresh_token: RefreshTokenDep,
    refresh_session: RefreshSessionDep,
    settings: SettingsDep,
    client_ip: ClientIpDep,
    user_agent: UserAgentDep,
) -> SessionResponse:
    session = await refresh_session.execute(
        refresh_token=refresh_token, client_ip=client_ip, user_agent=user_agent
    )
    set_session_cookies(response, session, settings)
    return SessionResponse(user=UserResponse.from_entity(session.user))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session",
    description="Revokes the current token family and clears both cookies. Idempotent.",
)
async def logout(
    response: Response,
    refresh_token: RefreshTokenDep,
    logout_session: LogoutSessionDep,
    client_ip: ClientIpDep,
) -> None:
    await logout_session.execute(refresh_token=refresh_token, client_ip=client_ip)
    clear_session_cookies(response)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="The authenticated account",
)
async def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.from_entity(current_user)


@router.post(
    "/password-reset",
    status_code=status.HTTP_202_ACCEPTED,
    # Without this the handler's `None` is serialised as the body
    # `null`, which is a value a client could come to depend on.
    response_class=EmptyResponse,
    summary="Request a password reset link",
    description=(
        "Always returns 202, whether or not an account exists for the address. "
        "Any other behaviour would make this endpoint an unauthenticated "
        "account-enumeration oracle. Heavily rate limited: each accepted "
        "request costs an outbound email to an address the caller chose."
    ),
)
async def request_password_reset(
    body: PasswordResetRequest,
    request_reset: RequestPasswordResetDep,
    client_ip: ClientIpDep,
) -> None:
    await request_reset.execute(email=body.email, client_ip=client_ip)


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set a new password with a reset token",
    description=(
        "Consumes the token, sets the password, and terminates every existing "
        "session for the account -- access tokens by bumping the token epoch, "
        "refresh tokens by explicit revocation. A reset that leaves the "
        "attacker's session alive has not recovered the account."
    ),
)
async def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    response: Response,
    complete_reset: CompletePasswordResetDep,
    client_ip: ClientIpDep,
) -> None:
    await complete_reset.execute(
        token=body.token, new_password=body.new_password, client_ip=client_ip
    )
    # The caller's own cookies are now dead server-side; clearing them stops
    # the browser sending a token that can only ever produce a 401.
    clear_session_cookies(response)


@router.post(
    "/verify-email/resend",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=EmptyResponse,
    summary="Resend the verification link",
    description="Requires a session. A no-op if the address is already verified.",
)
async def resend_email_verification(
    current_user: CurrentUserDep,
    request_verification: RequestEmailVerificationDep,
    client_ip: ClientIpDep,
) -> None:
    await request_verification.execute(user_id=current_user.id, client_ip=client_ip)


@router.post(
    "/verify-email/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm an email address",
    description=(
        "Deliberately unauthenticated: the link is followed from a mail client, "
        "which may not be the browser holding the session. The token is the "
        "only credential required, and it proves exactly one thing -- control "
        "of the address."
    ),
)
async def confirm_email_verification(
    body: EmailVerificationConfirmRequest,
    verify_email: VerifyEmailDep,
    client_ip: ClientIpDep,
) -> None:
    await verify_email.execute(token=body.token, client_ip=client_ip)
