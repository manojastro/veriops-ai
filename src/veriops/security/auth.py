"""Authentication (JWT) and role-based access control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import bcrypt
import jwt

from veriops.domain.enums import Role
from veriops.shared.clock import utcnow
from veriops.shared.config import Settings

ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"incidents:read", "knowledge:read", "evaluations:read"}),
    Role.ENGINEER: frozenset(
        {
            "incidents:read",
            "incidents:write",
            "incidents:investigate",
            "knowledge:read",
            "knowledge:write",
            "evaluations:read",
            "evaluations:run",
        }
    ),
    Role.APPROVER: frozenset(
        {
            "incidents:read",
            "incidents:write",
            "incidents:investigate",
            "incidents:approve",
            "incidents:execute",
            "knowledge:read",
            "evaluations:read",
            "evaluations:run",
        }
    ),
    Role.ADMINISTRATOR: frozenset({"*"}),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role: Role

    def can(self, permission: str) -> bool:
        granted = ROLE_PERMISSIONS[self.role]
        return "*" in granted or permission in granted

    @property
    def permissions(self) -> list[str]:
        granted = ROLE_PERMISSIONS[self.role]
        if "*" in granted:
            return sorted({p for perms in ROLE_PERMISSIONS.values() for p in perms if p != "*"} | {"settings:write"})
        return sorted(granted)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def issue_token(principal: Principal, settings: Settings) -> str:
    now = utcnow()
    payload = {
        "sub": principal.user_id,
        "username": principal.username,
        "name": principal.display_name,
        "role": principal.role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expiry_minutes)).timestamp()),
        "iss": "veriops",
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


class AuthError(Exception):
    pass


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer="veriops",
            options={"require": ["exp", "sub", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
    try:
        role = Role(payload["role"])
    except ValueError as exc:
        raise AuthError("unknown role") from exc
    return Principal(
        user_id=payload["sub"], username=payload.get("username", ""), display_name=payload.get("name", ""), role=role
    )
