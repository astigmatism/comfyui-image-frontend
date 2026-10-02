from typing import Annotated

from fastapi import Query

from ..errors import AppError
from ..schemas import CheckpointRank, GalleryFilters


def gallery_filters(
    favorites_only: bool = False,
    unfavorited_only: bool = False,
    excluded_checkpoint_ranks: Annotated[list[CheckpointRank] | None, Query()] = None,
) -> GalleryFilters:
    if favorites_only and unfavorited_only:
        raise AppError(
            "invalid_scope",
            "A view is filtered to favorites or to unfavorited items, not both.",
            status_code=422,
        )
    return GalleryFilters(
        favorites_only=favorites_only,
        unfavorited_only=unfavorited_only,
        excluded_checkpoint_ranks=excluded_checkpoint_ranks or [],
    )
