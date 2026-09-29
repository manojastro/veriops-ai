"""FastAPI dependencies: runtime access, authentication and permission checks."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from veriops.api.runtime import Runtime, get_runtime
from veriops.security.auth import AuthError, Principal, decode_token

bearer = HTTPBearer(auto_error=False)


def runtime_dep() -> Runtime:
    return get_runtime()


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    access_token: str | None = Query(default=None, description="Only for EventSource (SSE) clients"),
) -> Principal:
    token = credentials.credentials if credentials else None
    # Query-string tokens are accepted only on streaming endpoints (EventSource cannot set headers).
    if token is None and access_token and request.url.path.endswith("/stream"):
        token = access_token
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "authentication required", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        principal = decode_token(token, get_runtime().settings)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    request.state.principal = principal
    return principal


def require(permission: str) -> Callable[[Principal], Principal]:
    def checker(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{principal.role.value}' lacks '{permission}'")
        return principal

    return checker
