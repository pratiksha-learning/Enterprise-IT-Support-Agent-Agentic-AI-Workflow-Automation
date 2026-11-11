"""FastAPI dependencies: DB session (unit of work), services, principal and RBAC checks."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.container import Container, Services, build_services
from app.errors import AuthenticationError, AuthorizationError
from app.security.principal import Principal
from app.security.rbac import Permission, has_permission


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_db(request: Request) -> Iterator[Session]:
    """One transaction per request: commit on success, rollback on error."""
    db = request.app.state.session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_services(db: Session = Depends(get_db), container: Container = Depends(get_container)) -> Services:
    return build_services(db, container)


def optional_principal(request: Request) -> Principal | None:
    if getattr(request.state, "auth_error", None):
        raise request.state.auth_error
    return getattr(request.state, "principal", None)


def current_principal(request: Request) -> Principal:
    principal = optional_principal(request)
    if principal is None:
        raise AuthenticationError("Authentication required", code="UNAUTHENTICATED",
                                  headers={"WWW-Authenticate": "Bearer"})
    return principal


def require(permission: Permission):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not has_permission(principal, permission):
            raise AuthorizationError(f"Missing permission: {permission.value}", code="FORBIDDEN")
        return principal

    return _dep
