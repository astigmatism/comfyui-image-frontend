from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..dependencies import AuthContext, database_handler, get_container, get_db, require_ready_user
from ..models import ComfyUIInstanceHealth, ServiceHealth
from ..schemas import ComfyUIInstanceList, ComfyUIInstanceStatus, ImageWorkerPoolStatus

router = APIRouter(prefix="/api", tags=["comfyui-instances"])


@router.get("/comfyui-instances", response_model=ComfyUIInstanceList)
@database_handler
def list_comfyui_instances(
    request: Request,
    session: Annotated[Session, Depends(get_db, scope="function")],
    _: Annotated[AuthContext, Depends(require_ready_user)],
) -> ComfyUIInstanceList:
    container = get_container(request)
    instances = container.comfyui_instances
    pool = container.image_pool
    catalog_health = session.get(ServiceHealth, "comfyui")
    snapshot = pool.snapshot(session)
    occupancy = {worker.id: worker for worker in snapshot.workers}
    items: list[ComfyUIInstanceStatus] = []
    for config in instances.configs:
        health = session.get(ComfyUIInstanceHealth, config.id)
        if health is None and config.id == instances.default_id:
            available = bool(catalog_health and catalog_health.available)
            message = (
                catalog_health.message
                if catalog_health is not None
                else "Instance availability has not been checked yet."
            )
            checked_at = catalog_health.checked_at if catalog_health is not None else None
        else:
            available = bool(health and health.available)
            message = (
                health.message
                if health is not None
                else "Instance availability has not been checked yet."
            )
            checked_at = health.checked_at if health is not None else None
        items.append(
            ComfyUIInstanceStatus(
                id=config.id,
                label=config.label,
                description=config.description,
                is_default=config.id == instances.default_id,
                available=available,
                message=message,
                checked_at=checked_at,
                role=pool.role(config.id),
                in_image_pool=pool.is_member(config.id),
                busy=bool(occupancy[config.id].busy) if config.id in occupancy else False,
            )
        )
    return ComfyUIInstanceList(
        default_instance_id=instances.default_id,
        text_instance_id=instances.settings.comfyui_text_instance_id,
        configuration_mode=instances.configuration_mode,
        image_pool=ImageWorkerPoolStatus.model_validate(snapshot.payload()),
        image_pool_instance_ids=list(pool.member_ids),
        items=items,
    )
