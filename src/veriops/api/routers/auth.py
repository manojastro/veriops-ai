"""Authentication routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from veriops.api.deps import current_principal, runtime_dep
from veriops.api.runtime import Runtime
from veriops.api.schemas import LoginRequest, TokenResponse
from veriops.db.models import UserRow
from veriops.db.session import session_scope
from veriops.domain.enums import ActorType, Role
from veriops.domain.models import AuditRecord
from veriops.security.auth import Principal, issue_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_payload(p: Principal) -> dict[str, Any]:
    return {
        "id": p.user_id,
        "username": p.username,
        "display_name": p.display_name,
        "role": p.role.value,
        "permissions": p.permissions,
    }


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, rt: Runtime = Depends(runtime_dep)) -> TokenResponse:
    with session_scope() as s:
        user = s.scalar(select(UserRow).where(UserRow.username == body.username, UserRow.active.is_(True)))
        valid = user is not None and verify_password(body.password, user.password_hash)
        principal = Principal(user.id, user.username, user.display_name, Role(user.role)) if valid and user else None
    rt.recorder.audit(
        AuditRecord(
            incident_id=None,
            actor=body.username,
            actor_type=ActorType.HUMAN,
            action="auth.login",
            decision="SUCCESS" if principal else "FAILURE",
        )
    )
    if principal is None:
        raise HTTPException(401, "invalid credentials")
    return TokenResponse(access_token=issue_token(principal, rt.settings), user=_user_payload(principal))


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    return _user_payload(principal)
