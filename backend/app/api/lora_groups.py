from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictInt
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..dependencies import (
    AuthContext,
    database_handler,
    get_container,
    get_db,
    require_ready_user,
)
from ..errors import AppError
from ..schemas import APIModel
from ..services import lora_groups as groups
from ..services.lora_images import control_identities
from ..services.lora_library import Library, catalog_identities, libraries, library_key
from .admin import _admin_csrf

router = APIRouter(prefix="/api", tags=["lora-groups"])


class GroupRevision(APIModel):
    revision: StrictInt = Field(ge=0)


class GroupName(GroupRevision):
    name: str = Field(min_length=1, max_length=80)


class GroupAssignment(GroupRevision):
    group_id: str = Field(min_length=1, max_length=36)


def _library(request: Request, session: Session, key: str) -> Library:
    container = get_container(request)
    library = libraries(session, container.image_pool.primary_id).get(key)
    if library is None:
        raise AppError("lora_library_not_found", "LoRA library was not found.", status_code=404)
    return library


@router.get("/workflows/{source_key}/lora-groups/{control_id}")
@database_handler
def workflow_groups(
    source_key: str,
    control_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> dict[str, Any]:
    profile = get_container(request).registry.resolve_source(
        session, source_key=source_key, require_dependencies=False
    )
    key, _label = library_key(profile.manifest_json)
    return groups.grouping(session, key, control_identities(profile, control_id).values())


@router.get("/lora-library/{key}/groups")
@database_handler
def library_groups(
    key: str,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> dict[str, Any]:
    library = _library(request, session, key)
    return groups.grouping(session, key, catalog_identities(library.canonical).values())


def _begin(request: Request, session: Session, key: str, revision: int) -> Library:
    session.execute(text("BEGIN IMMEDIATE"))
    library = _library(request, session, key)
    groups.check_revision(session, key, revision)
    return library


def _finish(session: Session, library: Library) -> dict[str, Any]:
    session.flush()
    result = groups.grouping(session, library.key, catalog_identities(library.canonical).values())
    session.commit()
    return result


@router.post("/admin/lora-library/{key}/groups", status_code=201)
@database_handler
def create_group(
    key: str,
    payload: GroupName,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(_admin_csrf)],
) -> dict[str, Any]:
    library = _begin(request, session, key, payload.revision)
    groups.save_name(session, key, payload.name, None)
    return _finish(session, library)


@router.patch("/admin/lora-library/{key}/groups/{group_id}")
@database_handler
def rename_group(
    key: str,
    group_id: str,
    payload: GroupName,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(_admin_csrf)],
) -> dict[str, Any]:
    library = _begin(request, session, key, payload.revision)
    groups.save_name(session, key, payload.name, group_id)
    return _finish(session, library)


@router.delete("/admin/lora-library/{key}/groups/{group_id}")
@database_handler
def delete_group(
    key: str,
    group_id: str,
    payload: GroupRevision,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(_admin_csrf)],
) -> dict[str, Any]:
    library = _begin(request, session, key, payload.revision)
    session.delete(groups.named_group(session, key, group_id))
    return _finish(session, library)


@router.put("/admin/lora-library/{key}/group-memberships/{identity}")
@database_handler
def assign_group(
    key: str,
    identity: str,
    payload: GroupAssignment,
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(_admin_csrf)],
) -> dict[str, Any]:
    library = _begin(request, session, key, payload.revision)
    if identity not in catalog_identities(library.canonical).values():
        raise AppError("lora_not_found", "LoRA was not found in this library.", status_code=404)
    groups.assign(session, key, identity, payload.group_id)
    return _finish(session, library)
